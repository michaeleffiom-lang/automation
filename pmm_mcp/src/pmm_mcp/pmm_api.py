"""Read-only PMM and Prometheus-compatible API client."""

from __future__ import annotations

import base64
import json
import re
import ssl
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from .config import PMMClientConfig
from .service_discovery import merge_mongodb_inventory, mongodb_services


_PROMQL_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_PROMQL_STEP = re.compile(r"^(?:\d+(?:\.\d+)?[smhdwy]|\d+)$")


class PMMAPIError(RuntimeError):
    """A safe, user-facing PMM API error."""


class PMMClient:
    """Client for inventory, service discovery, and read-only PromQL queries."""

    def __init__(self, config: PMMClientConfig) -> None:
        self.config = config

    def _authorization(self) -> str | None:
        if self.config.token:
            return f"Bearer {self.config.token}"
        if self.config.username and self.config.password:
            raw = f"{self.config.username}:{self.config.password}".encode()
            return f"Basic {base64.b64encode(raw).decode()}"
        return None

    def _request(self, url: str, accept: str) -> str:
        headers = {"Accept": accept}
        authorization = self._authorization()
        if authorization:
            headers["Authorization"] = authorization
        request = Request(url, headers=headers, method="GET")
        context = ssl._create_unverified_context() if self.config.tls_insecure else None
        try:
            with urlopen(request, timeout=self.config.timeout, context=context) as response:
                return response.read().decode("utf-8", errors="replace")
        except HTTPError as exc:
            if exc.code == 401:
                raise PMMAPIError(f"Cliente '{self.config.client_id}': autenticação PMM rejeitada.") from exc
            if exc.code == 403:
                raise PMMAPIError(
                    f"Cliente '{self.config.client_id}': acesso negado ao endpoint PMM (403)."
                ) from exc
            raise PMMAPIError(
                f"Cliente '{self.config.client_id}': PMM retornou HTTP {exc.code}."
            ) from exc
        except URLError as exc:
            raise PMMAPIError(
                f"Cliente '{self.config.client_id}': não foi possível conectar ao PMM ({exc.reason})."
            ) from exc

    def _json_value(self, url: str) -> Any:
        try:
            payload = json.loads(self._request(url, "application/json"))
        except json.JSONDecodeError as exc:
            raise PMMAPIError(f"Cliente '{self.config.client_id}': resposta JSON inválida do PMM.") from exc
        if isinstance(payload, dict) and payload.get("status") == "error":
            raise PMMAPIError(f"Cliente '{self.config.client_id}': {payload.get('error', 'erro PMM')}")
        return payload

    def _json(self, url: str) -> dict[str, Any]:
        payload = self._json_value(url)
        if not isinstance(payload, dict):
            raise PMMAPIError(f"Cliente '{self.config.client_id}': resposta inesperada do PMM.")
        return payload

    def inventory(self, kind: str) -> dict[str, Any]:
        return self._json(f"{self.config.api_base_url}/{kind.lstrip('/')}")

    def version(self) -> dict[str, Any]:
        """Validate the connection and return PMM version metadata."""

        return self._json(f"{self.config.api_base_url}/version")

    def service_discovery(self) -> list[dict[str, Any]]:
        return mongodb_services(self._request(self.config.service_discovery_url, "text/html,application/xhtml+xml"))

    def grafana_dashboards(self) -> list[dict[str, Any]]:
        """Return the Grafana dashboard index exposed by PMM."""

        payload = self._json_value(
            f"{self.config.base_url}/graph/api/search?type=dash-db"
        )
        if not isinstance(payload, list):
            raise PMMAPIError(
                f"Cliente '{self.config.client_id}': índice de dashboards Grafana inválido."
            )
        return [item for item in payload if isinstance(item, dict)]

    def grafana_dashboard(self, uid: str) -> dict[str, Any]:
        """Return one complete Grafana dashboard by UID."""

        payload = self._json(
            f"{self.config.base_url}/graph/api/dashboards/uid/{quote(uid, safe='')}"
        )
        dashboard = payload.get("dashboard")
        if not isinstance(dashboard, dict):
            raise PMMAPIError(
                f"Cliente '{self.config.client_id}': dashboard Grafana sem conteúdo válido."
            )
        return dashboard

    def mongodb_services(self) -> list[dict[str, Any]]:
        services = self.inventory("inventory/services")
        agents = self.inventory("inventory/agents")
        targets = self.service_discovery()
        return merge_mongodb_inventory(services, agents, targets)

    def metric_names(self, search: str = "", limit: int = 500) -> list[str]:
        if len(search) > 200:
            raise ValueError("O filtro de métricas deve ter no máximo 200 caracteres.")
        payload = self._json(
            f"{self.config.base_url}/prometheus/api/v1/label/__name__/values"
        )
        values = payload.get("data", [])
        if not isinstance(values, list):
            return []
        normalized = [str(value) for value in values]
        if search:
            needle = search.lower()
            normalized = [value for value in normalized if needle in value.lower()]
        return sorted(normalized)[: max(1, min(limit, 50000))]

    def prometheus_query(
        self,
        query: str,
        start: str | None = None,
        end: str | None = None,
        step: str = "1m",
        max_results: int = 1000,
    ) -> dict[str, Any]:
        validate_promql(query)
        if (start is None) != (end is None):
            raise ValueError("Informe start e end juntos para uma consulta de intervalo.")
        max_results = max(1, min(max_results, 5000))
        if start is None:
            endpoint = f"{self.config.base_url}/prometheus/api/v1/query"
            params = {"query": query}
            mode = "instant"
        else:
            if not _PROMQL_STEP.match(step):
                raise ValueError("step deve ser uma duração Prometheus, como 30s, 5m ou 1h.")
            endpoint = f"{self.config.base_url}/prometheus/api/v1/query_range"
            params = {"query": query, "start": start, "end": end, "step": step}
            mode = "range"
        payload = self._json(f"{endpoint}?{urlencode(params)}")
        data = payload.get("data", {})
        if not isinstance(data, dict):
            raise PMMAPIError("Resposta Prometheus sem estrutura data válida.")
        result = data.get("result", [])
        if isinstance(result, list) and len(result) > max_results:
            data = dict(data)
            data["result"] = result[:max_results]
            data["truncated"] = True
        return {
            "client_id": self.config.client_id,
            "mode": mode,
            "query": query,
            "result_type": data.get("resultType", ""),
            "data": data,
        }


def validate_promql(query: str) -> None:
    """Apply lightweight safety limits to read-only PromQL investigation."""

    if not query or not query.strip():
        raise ValueError("A consulta PromQL não pode ser vazia.")
    if len(query) > 4096:
        raise ValueError("A consulta PromQL deve ter no máximo 4096 caracteres.")
    if _PROMQL_CONTROL_CHARS.search(query) or ";" in query:
        raise ValueError("A consulta PromQL contém caracteres não permitidos.")
