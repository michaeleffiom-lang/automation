"""MCP server for multi-client Percona PMM operations."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
import re
from typing import Any
from urllib.parse import urlsplit

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

from .config import PMMClientConfig, load_clients
from .dashboard_catalog import catalog_summary, load_catalog
from .pmm_api import PMMAPIError, PMMClient, validate_promql
from .performance_report import (
    build_replica_set_report,
    service_groups,
    summarize_member_series,
)
from .workload_comparison import compare_summaries, summarize_range_result


load_dotenv()
load_dotenv(Path(__file__).resolve().parents[2] / ".env")

mcp = FastMCP(
    "PMM Operations",
    instructions=(
        "MCP de operações do Percona PMM. O usuário pode ativar o modo PMM dizendo "
        "'A partir de agora use pmm mcp:'; nesse caso chame activate_pmm_mcp uma vez e use este servidor "
        "nos pedidos seguintes da mesma sessão. Um pedido que comece com 'use pmm mcp:' também autoriza o uso "
        "imediato. Se nenhum cliente estiver selecionado, pergunte primeiro a URL do PMM e use "
        "connect_pmm_client. Use o inventário para identificar clientes, "
        "clusters, nós e serviços MongoDB. Use PromQL somente para investigação. "
        "Ao conectar, carregue o catálogo de métricas e dashboards; consulte-o antes de repetir chamadas ao PMM. "
        "O health check padrão ainda não possui regras configuradas."
    ),
)

_RUNTIME_CLIENTS: dict[str, PMMClient] = {}
_RUNTIME_CATALOGS: dict[str, dict[str, Any]] = {}
_PMM_MODE_ACTIVE = False

_REPORT_THRESHOLDS = {
    "cpu_used_percent": 80.0,
    "root_filesystem_used_percent": 80.0,
    "connection_used_percent": 60.0,
    "wiredtiger_dirty_page_percent": 20.0,
}


def _report_selector(service_names: list[str]) -> str:
    """Build a bounded PromQL matcher for inventory service names."""

    def escape_re2(value: str) -> str:
        # Hyphen is literal outside a character class; Python's re.escape emits
        # ``\\-`` which RE2 rejects in this context.
        return re.sub(r"([\\.^$|?*+()\[\]{}])", r"\\\1", value)

    escaped = "|".join(escape_re2(name) for name in service_names if name)
    # Prometheus uses RE2, which does not support Python's ``(?:...)`` group.
    return f'service_name=~"^({escaped})$"' if escaped else ""


def _report_metric_query(
    available_metrics: set[str], metric: str, service_names: list[str]
) -> tuple[str | None, str | None]:
    """Choose a known PMM metric and the report expression for it.

    The labels and metric names vary across PMM/exporter releases.  A family is
    marked unavailable when its required metric is absent rather than querying a
    guessed identifier and interpreting the empty result as normal operation.
    """

    selector = _report_selector(service_names)
    definitions = {
        "cpu_used_percent": (
            ("node_cpu_seconds_total",),
            lambda names: f'100 * (1 - avg by (service_name) (rate({names[0]}{{mode="idle",{selector}}}[5m])))',
        ),
        "root_filesystem_used_percent": (
            ("node_filesystem_avail_bytes", "node_filesystem_size_bytes"),
            lambda names: f'100 * (1 - {names[0]}{{mountpoint="/",{selector}}} / {names[1]}{{mountpoint="/",{selector}}})',
        ),
        "connection_used_percent": (
            ("mongodb_connections",),
            lambda names: (
                f'100 * sum by (service_name) ({names[0]}{{state="current",{selector}}}) '
                f'/ (sum by (service_name) ({names[0]}{{state="current",{selector}}}) '
                f'+ sum by (service_name) ({names[0]}{{state="available",{selector}}}))'
            ),
        ),
        "wiredtiger_dirty_page_percent": (
            ("mongodb_wiredtiger_cache_pages_dirty", "mongodb_wiredtiger_cache_pages_total"),
            lambda names: f'100 * {names[0]}{{{selector}}} / {names[1]}{{{selector}}}',
        ),
        "replication_lag_seconds": (
            ("mongodb_mongod_replset_member_replication_lag",),
            lambda names: f'avg by (service_name) ({names[0]}{{{selector}}})',
        ),
        "operations_per_second": (
            ("mongodb_op_counters_total",),
            lambda names: f'sum by (service_name) (rate({names[0]}{{{selector}}}[5m]))',
        ),
    }
    candidates, expression = definitions[metric]
    if not selector or not all(candidate in available_metrics for candidate in candidates):
        return None, None
    return candidates[0], expression(candidates)


def _configured_clients() -> dict[str, PMMClientConfig]:
    return load_clients()


def _resolve_client(client_id: str) -> tuple[str, PMMClient]:
    clients = _configured_clients()
    requested = client_id.strip() or "default"
    if requested == "default":
        if "session" in _RUNTIME_CLIENTS:
            return "session", _RUNTIME_CLIENTS["session"]
        if len(clients) == 1:
            requested = next(iter(clients))
        else:
            raise ValueError(
                "Informe client_id. Clientes disponíveis: " + ", ".join(sorted(clients))
            )
    if requested in _RUNTIME_CLIENTS:
        return requested, _RUNTIME_CLIENTS[requested]
    config = clients.get(requested)
    if config is None:
        raise ValueError(
            f"Cliente PMM não encontrado: {requested}. "
            f"Disponíveis: {', '.join(sorted(clients))}"
        )
    return requested, PMMClient(config)


def _client_public_info(client_id: str, config: PMMClientConfig) -> dict[str, object]:
    return {
        "client_id": client_id,
        "base_url": config.base_url,
        "api_base_url": config.api_base_url,
        "service_discovery_url": config.service_discovery_url,
        "tls_insecure": config.tls_insecure,
        "has_authentication": bool(config.token or (config.username and config.password)),
    }


def _filter_services(
    services: list[dict[str, Any]],
    service_name: str = "",
    node_id: str = "",
    cluster: str = "",
    replication_set: str = "",
) -> list[dict[str, Any]]:
    filters = {
        "service_name": service_name.strip().lower(),
        "node_id": node_id.strip().lower(),
        "cluster": cluster.strip().lower(),
        "replication_set": replication_set.strip().lower(),
    }
    return [
        service
        for service in services
        if all(
            not value or value in str(service.get(field, "")).lower()
            for field, value in filters.items()
        )
    ]


def _client_catalog(
    resolved_id: str, client: PMMClient, refresh: bool = False
) -> tuple[dict[str, Any], bool]:
    if not refresh and resolved_id in _RUNTIME_CATALOGS:
        return _RUNTIME_CATALOGS[resolved_id], True
    catalog, cached = load_catalog(client, refresh=refresh)
    _RUNTIME_CATALOGS[resolved_id] = catalog
    return catalog, cached


@mcp.tool()
def activate_pmm_mcp() -> dict[str, object]:
    """Ativa o uso do MCP PMM para os próximos pedidos desta sessão."""

    global _PMM_MODE_ACTIVE
    _PMM_MODE_ACTIVE = True
    return {
        "active": True,
        "scope": "current_mcp_session",
        "instruction": "Os próximos pedidos PMM devem usar exclusivamente o servidor pmm-operations.",
    }


@mcp.tool()
def deactivate_pmm_mcp() -> dict[str, object]:
    """Desativa o modo PMM para os próximos pedidos desta sessão."""

    global _PMM_MODE_ACTIVE
    _PMM_MODE_ACTIVE = False
    return {"active": False, "scope": "current_mcp_session"}


def _runtime_config(
    client_id: str,
    url: str,
    username: str | None,
    password: str | None,
    token: str | None,
    tls_insecure: bool,
) -> PMMClientConfig:
    parsed = urlsplit(url.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Informe uma URL PMM válida, por exemplo https://cliente.example:8443.")
    if parsed.username or parsed.password:
        raise ValueError("Não inclua usuário ou senha na URL; informe-os separadamente.")
    base_url = f"{parsed.scheme}://{parsed.netloc}"
    return PMMClientConfig(
        client_id=client_id,
        base_url=base_url,
        api_base_url=f"{base_url}/v1",
        service_discovery_url=f"{base_url}/prometheus/service-discovery",
        username=username,
        password=password,
        token=token,
        tls_insecure=tls_insecure,
        timeout=float(os.getenv("PMM_HTTP_TIMEOUT", "10")),
    )


@mcp.tool()
def connect_pmm_client(
    url: str,
    client_id: str = "session",
    username: str | None = None,
    password: str | None = None,
    token: str | None = None,
    tls_insecure: bool = True,
) -> dict[str, object]:
    """Conecta temporariamente a um PMM informado pelo usuário.

    Use esta ferramenta depois de perguntar a URL. Credenciais não são persistidas
    e nunca são devolvidas no resultado.
    """

    resolved_id = client_id.strip() or "session"
    if token and (username or password):
        raise ValueError("Use token ou usuário/senha, não os dois métodos de autenticação.")

    # Para o PMM local, a URL é suficiente porque as credenciais padrão do
    # processo já estão no .env. Para clientes remotos, o agente pode reenviar
    # username/password ou token explicitamente.
    effective_username = username or os.getenv("PMM_DISCOVERY_USERNAME") or os.getenv(
        "PMM_ADMIN_USERNAME"
    )
    effective_password = password or os.getenv("PMM_DISCOVERY_PASSWORD") or os.getenv(
        "PMM_ADMIN_PASSWORD"
    )
    effective_token = token
    if not effective_token and not (effective_username and effective_password):
        effective_token = os.getenv("PMM_DISCOVERY_TOKEN") or os.getenv(
            "PMM_SERVICE_ACCOUNT_TOKEN"
        )
    config = _runtime_config(
        resolved_id,
        url,
        effective_username,
        effective_password,
        effective_token,
        tls_insecure,
    )
    client = PMMClient(config)
    version = client.version()
    catalog, catalog_cached = _client_catalog(resolved_id, client)
    _RUNTIME_CLIENTS[resolved_id] = client
    dashboard_metrics = catalog.get("metrics", {}).get("dashboard_available", {})
    return {
        "connected": True,
        "client_id": resolved_id,
        "base_url": config.base_url,
        "api_base_url": config.api_base_url,
        "service_discovery_url": config.service_discovery_url,
        "pmm_version": version.get("version", ""),
        "authentication": (
            "bearer"
            if effective_token
            else "basic"
            if effective_username and effective_password
            else "none"
        ),
        "credentials_persisted": False,
        "metric_catalog": {
            "ready": True,
            "cached": catalog_cached,
            "generated_at": catalog.get("generated_at", ""),
            "prometheus_metric_count": len(
                catalog.get("metrics", {}).get("prometheus", [])
            ),
            "hardware_dashboard_metric_count": len(
                dashboard_metrics.get("hardware", [])
            ),
            "mongodb_dashboard_metric_count": len(
                dashboard_metrics.get("mongodb", [])
            ),
            "hardware_metrics": dashboard_metrics.get("hardware", []),
            "mongodb_metrics": dashboard_metrics.get("mongodb", []),
        },
    }


@mcp.tool()
def disconnect_pmm_client(client_id: str = "session") -> dict[str, object]:
    """Remove uma conexão temporária da memória do processo MCP."""

    resolved_id = client_id.strip() or "session"
    removed = _RUNTIME_CLIENTS.pop(resolved_id, None) is not None
    _RUNTIME_CATALOGS.pop(resolved_id, None)
    return {"disconnected": removed, "client_id": resolved_id}


@mcp.tool()
def list_pmm_clients() -> list[dict[str, object]]:
    """Lista os clientes PMM configurados sem expor credenciais."""

    configured = [
        _client_public_info(client_id, config)
        for client_id, config in sorted(_configured_clients().items())
    ]
    runtime = [
        _client_public_info(client_id, client.config)
        for client_id, client in sorted(_RUNTIME_CLIENTS.items())
        if client_id not in {item["client_id"] for item in configured}
    ]
    return configured + runtime


@mcp.tool()
def discover_environment(client_id: str = "default") -> dict[str, object]:
    """Descobre o ambiente MongoDB de um cliente PMM de forma normalizada."""

    resolved_id, client = _resolve_client(client_id)
    services = client.mongodb_services()
    config = client.config
    clusters = sorted({str(item["cluster"]) for item in services if item.get("cluster")})
    replica_sets = sorted(
        {str(item["replication_set"]) for item in services if item.get("replication_set")}
    )
    nodes = sorted({str(item["node_id"]) for item in services if item.get("node_id")})
    return {
        "schema_version": "0.1.0",
        "client": _client_public_info(resolved_id, config),
        "discovered_at": datetime.now(UTC).isoformat(),
        "scope": {
            "clusters": clusters,
            "replica_sets": replica_sets,
            "nodes": nodes,
        },
        "services": services,
        "counts": {
            "mongodb_services": len(services),
            "clusters": len(clusters),
            "replica_sets": len(replica_sets),
            "nodes": len(nodes),
        },
    }


@mcp.tool()
def discover_mongodb_services(
    client_id: str = "default",
    service_name: str = "",
    node_id: str = "",
    cluster: str = "",
    replication_set: str = "",
) -> list[dict[str, object]]:
    """Lista MongoDB de um cliente PMM, com filtros opcionais simples."""

    _, client = _resolve_client(client_id)
    return _filter_services(
        client.mongodb_services(), service_name, node_id, cluster, replication_set
    )


@mcp.tool()
def get_mongodb_service(service: str, client_id: str = "default") -> dict[str, object]:
    """Retorna um MongoDB pelo nome, service_id, endereço ou replica set."""

    query = service.strip().lower()
    if not query:
        raise ValueError("Informe service_name, service_id, endereço ou replica set.")
    _, client = _resolve_client(client_id)
    services = client.mongodb_services()
    matches = [
        item
        for item in services
        if query in {
            str(item.get("service_name", "")).lower(),
            str(item.get("service_id", "")).lower(),
            str(item.get("address", "")).lower(),
            str(item.get("cluster", "")).lower(),
            str(item.get("replication_set", "")).lower(),
        }
    ]
    if not matches:
        raise ValueError(f"MongoDB não encontrado no cliente '{client_id}': {service}")
    return matches[0]


@mcp.tool()
def discover_prometheus_metrics(
    client_id: str = "default",
    search: str = "",
    limit: int = 100,
    category: str = "all",
) -> list[str]:
    """Lista métricas do catálogo local sem consultar o PMM a cada chamada.

    ``category`` pode ser ``all``, ``hardware`` ou ``mongodb``. As duas últimas
    opções retornam somente métricas encontradas nas queries dos dashboards
    Grafana correspondentes.
    """

    resolved_id, client = _resolve_client(client_id)
    catalog, _ = _client_catalog(resolved_id, client)
    category = category.strip().lower() or "all"
    if category == "all":
        names = catalog.get("metrics", {}).get("prometheus", [])
    elif category in {"hardware", "mongodb"}:
        names = catalog.get("metrics", {}).get("dashboard_available", {}).get(category, [])
    else:
        raise ValueError("category deve ser all, hardware ou mongodb.")
    names = [str(name) for name in names]
    if search:
        needle = search.strip().lower()
        names = [name for name in names if needle in name.lower()]
    return sorted(names)[: max(1, min(limit, 1000))]


@mcp.tool()
def refresh_dashboard_catalog(client_id: str = "default") -> dict[str, object]:
    """Atualiza explicitamente o catálogo de métricas e dashboards Grafana."""

    resolved_id, client = _resolve_client(client_id)
    catalog, _ = _client_catalog(resolved_id, client, refresh=True)
    return catalog_summary(catalog, cached=False)


@mcp.tool()
def discover_dashboard_metrics(
    client_id: str = "default",
    category: str = "all",
    search: str = "",
    dashboard_uid: str = "",
    limit: int = 200,
) -> dict[str, object]:
    """Consulta o catálogo local de métricas usadas nos gráficos Grafana.

    Retorna também os dashboards e painéis onde cada métrica é usada. O
    catálogo é carregado da memória/arquivo e não faz uma chamada ao PMM a
    cada consulta.
    """

    resolved_id, client = _resolve_client(client_id)
    catalog, cached = _client_catalog(resolved_id, client)
    category = category.strip().lower() or "all"
    if category not in {"all", "hardware", "mongodb"}:
        raise ValueError("category deve ser all, hardware ou mongodb.")
    dashboards = catalog.get("dashboards", {})
    selected = (
        [dashboard for items in dashboards.values() for dashboard in items]
        if category == "all"
        else dashboards.get(category, [])
    )
    if dashboard_uid.strip():
        selected = [
            dashboard
            for dashboard in selected
            if str(dashboard.get("uid", "")) == dashboard_uid.strip()
        ]
    search = search.strip().lower()
    usage: dict[str, dict[str, Any]] = {}
    available_metrics = set(
        metric
        for metrics in catalog.get("metrics", {}).get("dashboard_available", {}).values()
        for metric in metrics
    )
    for dashboard in selected:
        for chart in dashboard.get("charts", []):
            for metric in chart.get("metrics", []):
                if metric not in available_metrics:
                    continue
                if search and search not in str(metric).lower():
                    continue
                entry = usage.setdefault(
                    str(metric),
                    {"name": str(metric), "dashboards": [], "panels": []},
                )
                dashboard_ref = {
                    "uid": dashboard.get("uid", ""),
                    "title": dashboard.get("title", ""),
                    "category": dashboard.get("category", ""),
                }
                if dashboard_ref not in entry["dashboards"]:
                    entry["dashboards"].append(dashboard_ref)
                panel_ref = {
                    "dashboard_uid": dashboard.get("uid", ""),
                    "dashboard_title": dashboard.get("title", ""),
                    "panel_id": chart.get("panel_id", ""),
                    "panel_title": chart.get("title", ""),
                }
                if panel_ref not in entry["panels"]:
                    entry["panels"].append(panel_ref)
    metrics = sorted(usage.values(), key=lambda item: str(item["name"]))
    max_items = max(1, min(limit, 1000))
    return {
        "schema_version": catalog.get("schema_version", "0.1.0"),
        "client": _client_public_info(resolved_id, client.config),
        "generated_at": catalog.get("generated_at", ""),
        "cached": cached,
        "category": category,
        "metric_count": len(metrics),
        "metrics": metrics[:max_items],
    }


@mcp.tool()
def list_grafana_dashboards(
    client_id: str = "default",
    category: str = "all",
    search: str = "",
    limit: int = 100,
) -> list[dict[str, object]]:
    """Lista dashboards Grafana do catálogo local por categoria ou texto."""

    resolved_id, client = _resolve_client(client_id)
    catalog, _ = _client_catalog(resolved_id, client)
    category = category.strip().lower() or "all"
    if category not in {"all", "hardware", "mongodb"}:
        raise ValueError("category deve ser all, hardware ou mongodb.")
    dashboards = catalog.get("dashboards", {})
    selected = (
        [dashboard for items in dashboards.values() for dashboard in items]
        if category == "all"
        else dashboards.get(category, [])
    )
    search = search.strip().lower()
    result = [
        {
            "uid": dashboard.get("uid", ""),
            "title": dashboard.get("title", ""),
            "category": dashboard.get("category", ""),
            "folder": dashboard.get("folder", ""),
            "tags": dashboard.get("tags", []),
            "url": f"{client.config.base_url}/graph/d/{dashboard.get('uid', '')}",
            "chart_count": dashboard.get("chart_count", 0),
            "metric_count": dashboard.get(
                "available_metric_count", dashboard.get("metric_count", 0)
            ),
        }
        for dashboard in selected
        if not search
        or search in str(dashboard.get("title", "")).lower()
        or search in str(dashboard.get("uid", "")).lower()
    ]
    return result[: max(1, min(limit, 500))]


@mcp.tool()
def get_grafana_dashboard(
    dashboard_uid: str, client_id: str = "default"
) -> dict[str, object]:
    """Retorna os painéis, métricas e PromQL de um dashboard em cache."""

    if not dashboard_uid.strip():
        raise ValueError("Informe o UID do dashboard Grafana.")
    resolved_id, client = _resolve_client(client_id)
    catalog, cached = _client_catalog(resolved_id, client)
    for dashboards in catalog.get("dashboards", {}).values():
        for dashboard in dashboards:
            if str(dashboard.get("uid", "")) == dashboard_uid.strip():
                return {
                    "schema_version": catalog.get("schema_version", "0.1.0"),
                    "client": _client_public_info(resolved_id, client.config),
                    "generated_at": catalog.get("generated_at", ""),
                    "cached": cached,
                    "dashboard": dashboard,
                }
    raise ValueError(f"Dashboard Grafana não encontrado no catálogo: {dashboard_uid}")


@mcp.tool()
def query_prometheus(
    client_id: str,
    query: str,
    start: str | None = None,
    end: str | None = None,
    step: str = "1m",
    max_results: int = 1000,
) -> dict[str, object]:
    """Executa uma consulta PromQL somente leitura, instantânea ou por intervalo."""

    _, client = _resolve_client(client_id)
    return client.prometheus_query(query, start, end, step, max_results)


@mcp.tool()
def compare_workload_windows(
    client_id: str,
    queries: list[dict[str, str]],
    baseline_start: str,
    baseline_end: str,
    comparison_start: str,
    comparison_end: str,
    step: str = "1m",
    change_threshold_percent: float = 10.0,
    max_results: int = 1000,
) -> dict[str, object]:
    """Compara métricas PromQL entre uma janela baseline e uma janela atual.

    ``queries`` contém itens com ``name`` e ``query``. O resultado é uma
    investigação de performance, não altera o health check padrão.
    """

    if not queries:
        raise ValueError("Informe pelo menos uma métrica em queries.")
    if len(queries) > 50:
        raise ValueError("A comparação aceita no máximo 50 métricas por relatório.")
    if not 0 <= change_threshold_percent <= 100000:
        raise ValueError("change_threshold_percent deve estar entre 0 e 100000.")
    normalized: list[tuple[str, str]] = []
    for item in queries:
        if not isinstance(item, dict):
            raise ValueError("Cada item de queries deve conter name e query.")
        name = str(item.get("name", "")).strip()
        query = str(item.get("query", "")).strip()
        if not name or not query:
            raise ValueError("Cada métrica precisa de name e query não vazios.")
        validate_promql(query)
        normalized.append((name, query))

    resolved_id, client = _resolve_client(client_id)
    metrics: list[dict[str, object]] = []
    for name, query in normalized:
        baseline_result = client.prometheus_query(
            query, baseline_start, baseline_end, step, max_results
        )
        comparison_result = client.prometheus_query(
            query, comparison_start, comparison_end, step, max_results
        )
        baseline = summarize_range_result(baseline_result)
        comparison = summarize_range_result(comparison_result)
        change = compare_summaries(baseline, comparison, change_threshold_percent)
        metrics.append({
            "name": name, "query": query, "baseline": baseline,
            "comparison": comparison, "change": change,
        })
    changed = [item for item in metrics if item["change"]["changed"]]
    unavailable = [item for item in metrics if item["change"]["status"] == "unavailable"]
    return {
        "schema_version": "0.1.0",
        "report_type": "workload_window_comparison",
        "standard": False,
        "investigation": True,
        "generated_at": datetime.now(UTC).isoformat(),
        "client": _client_public_info(resolved_id, client.config),
        "windows": {
            "baseline": {"start": baseline_start, "end": baseline_end},
            "comparison": {"start": comparison_start, "end": comparison_end},
            "step": step,
        },
        "change_threshold_percent": change_threshold_percent,
        "metrics": metrics,
        "summary": {
            "total_metrics": len(metrics), "changed_metrics": len(changed),
            "stable_metrics": len(metrics) - len(changed) - len(unavailable),
            "unavailable_metrics": len(unavailable),
        },
        "interpretation": (
            "Mudanças são comparações estatísticas entre janelas e não representam "
            "um diagnóstico nem alteram o health check padrão."
        ),
    }


@mcp.tool()
def generate_performance_reports(
    client_id: str = "default",
    period_hours: int = 168,
    end: str = "",
    step: str = "1h",
    cluster: str = "",
    replication_set: str = "",
) -> dict[str, object]:
    """Gera uma avaliação de performance por cluster/replica set a partir do PMM.

    O resultado segue a estrutura de uma avaliação Percona Managed Services:
    condição observada, avaliação, relevância ao cliente e foco recomendado.
    Ele é uma investigação somente leitura e não substitui o health check padrão.
    Métricas indisponíveis permanecem explícitas no relatório.
    """

    if not 1 <= period_hours <= 24 * 90:
        raise ValueError("period_hours deve estar entre 1 e 2160 (90 dias).")
    resolved_id, client = _resolve_client(client_id)
    services = _filter_services(
        client.mongodb_services(), cluster=cluster, replication_set=replication_set
    )
    if not services:
        raise ValueError("Nenhum serviço MongoDB corresponde ao cluster ou replica set informado.")
    if end.strip():
        try:
            end_for_start = datetime.fromisoformat(end.strip().replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("end deve usar ISO-8601, por exemplo 2026-10-06T20:30:25Z.") from exc
        if end_for_start.tzinfo is None:
            raise ValueError("end deve incluir timezone, por exemplo o sufixo Z para UTC.")
        period_end = end_for_start.astimezone(UTC).isoformat().replace("+00:00", "Z")
    else:
        end_for_start = datetime.now(UTC)
        period_end = end_for_start.isoformat().replace("+00:00", "Z")
    period_start = (end_for_start - timedelta(hours=period_hours)).astimezone(UTC).isoformat().replace("+00:00", "Z")

    catalog, _ = _client_catalog(resolved_id, client)
    available_metrics = {
        str(name) for name in catalog.get("metrics", {}).get("prometheus", [])
    }
    reports: list[dict[str, object]] = []
    for group in service_groups(services):
        members = list(group["members"])
        service_names = [str(member.get("service_name", "")) for member in members]
        metric_summaries: dict[str, dict[str, dict[str, object]]] = {}
        metric_sources: dict[str, str | None] = {}
        for metric in _REPORT_THRESHOLDS | {
            "replication_lag_seconds": 0.0,
            "operations_per_second": 0.0,
        }:
            source, query = _report_metric_query(available_metrics, metric, service_names)
            metric_sources[metric] = source
            if query is None:
                metric_summaries[metric] = {
                    name: {"status": "unavailable", "samples": 0, "latest": None, "mean": None, "maximum": None}
                    for name in service_names if name
                }
                continue
            try:
                result = client.prometheus_query(query, period_start, period_end, step, 5000)
            except PMMAPIError:
                # PMM/exporter releases differ in label schemas as well as metric
                # names. Preserve a report with an explicit telemetry gap instead
                # of losing every other independent metric family.
                metric_summaries[metric] = {
                    name: {"status": "unavailable", "samples": 0, "latest": None, "mean": None, "maximum": None}
                    for name in service_names if name
                }
                metric_sources[metric] = None
                continue
            metric_summaries[metric] = summarize_member_series(result, members)
        report = build_replica_set_report(
            str(group["cluster"]), str(group["replica_set"]), members, metric_summaries,
            _REPORT_THRESHOLDS,
        )
        report["period"] = {"start": period_start, "end": period_end, "step": step}
        report["metric_sources"] = metric_sources
        reports.append(report)
    return {
        "schema_version": "0.1.0",
        "report_type": "mongodb_cluster_performance_review_collection",
        "standard": False,
        "investigation": True,
        "generated_at": datetime.now(UTC).isoformat(),
        "client": _client_public_info(resolved_id, client.config),
        "scope": {"cluster": cluster, "replication_set": replication_set},
        "reports": reports,
        "interpretation": (
            "Thresholds are observational, and the assessment distinguishes available PMM data "
            "from operational interpretation; this report does not change the standard health check."
        ),
    }


@mcp.tool()
def health_check(
    client_id: str = "default",
    service_name: str = "",
    node_id: str = "",
    cluster: str = "",
    replication_set: str = "",
) -> dict[str, object]:
    """Retorna o contrato do health check padrão e o ambiente descoberto.

    As regras e métricas corporativas serão adicionadas quando forem definidas.
    Até lá, o resultado fica explicitamente como ``not_configured``.
    """

    environment = discover_environment(client_id)
    selected = _filter_services(
        list(environment["services"]), service_name, node_id, cluster, replication_set
    )
    return {
        "schema_version": "0.1.0",
        "report_type": "standard_health_check",
        "standard": True,
        "status": "not_configured",
        "rules_version": "draft",
        "generated_at": datetime.now(UTC).isoformat(),
        "client": environment["client"],
        "scope": {
            "service_name": service_name,
            "node_id": node_id,
            "cluster": cluster,
            "replication_set": replication_set,
        },
        "environment": {
            "mongodb_services": selected,
            "mongodb_service_count": len(selected),
        },
        "checks": [],
        "summary": {"total": 0, "passed": 0, "warning": 0, "critical": 0},
        "next_step": "Definir as métricas e limites oficiais do health check.",
    }


@mcp.resource("pmm://clients")
def clients_resource() -> str:
    """Snapshot JSON dos clientes PMM configurados."""

    return json.dumps(list_pmm_clients(), ensure_ascii=False, indent=2)


@mcp.resource("pmm://mongodb-services/{client_id}")
def mongodb_services_resource(client_id: str) -> str:
    """Snapshot JSON dos MongoDB de um cliente PMM."""

    return json.dumps(discover_mongodb_services(client_id), ensure_ascii=False, indent=2)


def main() -> None:
    transport = os.getenv("MCP_TRANSPORT", "stdio")
    mcp.run() if transport == "stdio" else mcp.run(transport=transport)


if __name__ == "__main__":
    main()
