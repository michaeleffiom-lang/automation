"""Configuration for one or more PMM client connections."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit


def _flag(value: object, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _env_or_value(config: dict[str, object], value_key: str, env_key: str) -> str | None:
    env_name = config.get(env_key)
    if env_name:
        return os.getenv(str(env_name))
    value = config.get(value_key)
    return str(value) if value is not None else None


@dataclass(frozen=True)
class PMMClientConfig:
    """Connection details for a PMM instance, without exposing secrets."""

    client_id: str
    base_url: str
    api_base_url: str
    service_discovery_url: str
    username: str | None
    password: str | None
    token: str | None
    tls_insecure: bool
    timeout: float

    @property
    def display_name(self) -> str:
        return self.client_id


def _from_mapping(client_id: str, raw: dict[str, object]) -> PMMClientConfig:
    discovery_url = str(
        raw.get(
            "service_discovery_url",
            f"{raw.get('base_url', 'https://devbox:8444').rstrip('/')}/prometheus/service-discovery",
        )
    )
    parsed = urlsplit(discovery_url)
    base_url = str(raw.get("base_url") or f"{parsed.scheme}://{parsed.netloc}").rstrip("/")
    api_base_url = str(raw.get("api_base_url") or f"{base_url}/v1").rstrip("/")
    auth = raw.get("auth") if isinstance(raw.get("auth"), dict) else {}
    auth = dict(auth)
    username = _env_or_value(auth, "username", "username_env")
    password = _env_or_value(auth, "password", "password_env")
    token = _env_or_value(auth, "token", "token_env")
    return PMMClientConfig(
        client_id=client_id,
        base_url=base_url,
        api_base_url=api_base_url,
        service_discovery_url=discovery_url,
        username=username,
        password=password,
        token=token,
        tls_insecure=_flag(raw.get("tls_insecure"), _flag(os.getenv("PMM_TLS_INSECURE"))),
        timeout=float(raw.get("timeout", os.getenv("PMM_HTTP_TIMEOUT", "10"))),
    )


def _default_config() -> PMMClientConfig:
    discovery_url = os.getenv(
        "PMM_SERVICE_DISCOVERY_URL",
        "https://devbox:8444/prometheus/service-discovery",
    )
    parsed = urlsplit(discovery_url)
    base_url = f"{parsed.scheme}://{parsed.netloc}"
    api_base_url = os.getenv("PMM_API_BASE_URL", f"{base_url}/v1")
    username = os.getenv("PMM_DISCOVERY_USERNAME") or os.getenv("PMM_ADMIN_USERNAME")
    password = os.getenv("PMM_DISCOVERY_PASSWORD") or os.getenv("PMM_ADMIN_PASSWORD")
    token = os.getenv("PMM_DISCOVERY_TOKEN")
    if not (username and password):
        token = token or os.getenv("PMM_SERVICE_ACCOUNT_TOKEN")
    return PMMClientConfig(
        client_id=os.getenv("PMM_CLIENT_ID", "default"),
        base_url=base_url,
        api_base_url=api_base_url.rstrip("/"),
        service_discovery_url=discovery_url,
        username=username,
        password=password,
        token=token,
        tls_insecure=_flag(os.getenv("PMM_TLS_INSECURE")),
        timeout=float(os.getenv("PMM_HTTP_TIMEOUT", "10")),
    )


def load_clients(path: str | os.PathLike[str] | None = None) -> dict[str, PMMClientConfig]:
    """Load configured PMM clients, falling back to the existing single-client env."""

    config_path = Path(path or os.getenv("PMM_CLIENTS_FILE", "clients.json"))
    if not config_path.is_absolute() and not config_path.exists():
        project_root = Path(__file__).resolve().parents[2]
        config_path = project_root / config_path
    if not config_path.exists():
        config = _default_config()
        return {config.client_id: config}

    raw = json.loads(config_path.read_text(encoding="utf-8"))
    clients = raw.get("clients", raw) if isinstance(raw, dict) else raw
    if not isinstance(clients, (dict, list)):
        raise ValueError("A configuração de clientes PMM deve conter um objeto ou lista em 'clients'.")

    result: dict[str, PMMClientConfig] = {}
    if isinstance(clients, dict):
        entries = [(str(key), value) for key, value in clients.items()]
    else:
        entries = [(str(value.get("id", "")), value) for value in clients if isinstance(value, dict)]

    for client_id, value in entries:
        if not client_id or not isinstance(value, dict):
            raise ValueError("Cada cliente PMM precisa de um identificador e uma configuração de objeto.")
        if client_id in result:
            raise ValueError(f"Cliente PMM duplicado: {client_id}")
        result[client_id] = _from_mapping(client_id, value)
    if not result:
        raise ValueError("Nenhum cliente PMM foi configurado.")
    return result
