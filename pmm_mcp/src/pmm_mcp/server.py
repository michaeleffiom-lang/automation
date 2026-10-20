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
    add_metric_coverage,
    apply_host_topology,
    attach_peak_detail_evidence,
    build_replica_set_report,
    correlate_workload_peaks,
    detect_member_workload_peaks,
    expected_range_samples,
    extract_member_observations,
    historical_role_at_timestamp,
    infer_host_topology,
    needs_host_label_fallback,
    prefer_more_complete_observations,
    service_groups,
    summarize_member_series,
    summarize_member_observations,
    summarize_member_role_series,
)
from .sampling import (
    bound_start_to_retention,
    parse_prometheus_duration,
    resolve_utc_window,
    select_peak_drilldown_step,
    select_report_step,
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

_HOST_METRICS = {
    "cpu_used_percent", "memory_used_percent", "root_filesystem_used_percent",
    "disk_iops", "disk_io_latency_seconds", "disk_read_iops", "disk_write_iops",
    "disk_read_latency_seconds", "disk_write_latency_seconds",
}


def _report_label_selector(label: str, values: list[str]) -> str:
    """Build a bounded PromQL matcher for values from PMM inventory."""

    # Hyphen is literal outside a character class; Python's re.escape emits
    # ``\\-`` which RE2 rejects in this context.
    escaped = "|".join(
        re.sub(r"([\\.^$|?*+()\[\]{}])", r"\\\1", value)
        for value in values if value
    )
    # Prometheus uses RE2, which does not support Python's ``(?:...)`` group.
    return f'{label}=~"^({escaped})$"' if escaped else ""


def _report_selector(service_names: list[str]) -> str:
    """Build a bounded PromQL matcher for inventory service names."""
    return _report_label_selector("service_name", service_names)


def _report_node_selector(
    members: list[dict[str, Any]], host_label_override: str | None = None
) -> tuple[str, str]:
    """Choose the host label that connects node-exporter metrics to inventory."""
    if host_label_override == "node_name":
        node_names = [str(member.get("node_name") or member.get("address") or "") for member in members]
        if node_names and all(node_names):
            return "node_name", _report_label_selector("node_name", node_names)
        return "", ""
    node_ids = [str(member.get("node_id", "")) for member in members]
    if node_ids and all(node_ids):
        return "node_id", _report_label_selector("node_id", node_ids)

    node_names = [
        str(member.get("node_name") or member.get("address") or "")
        for member in members
    ]
    if node_names and all(node_names):
        return "node_name", _report_label_selector("node_name", node_names)
    return "", ""


def _report_metric_query(
    available_metrics: set[str], metric: str, members: list[dict[str, Any]],
    host_label_override: str | None = None,
) -> tuple[str | None, str | None]:
    """Choose a known PMM metric and the report expression for it.

    The labels and metric names vary across PMM/exporter releases.  A family is
    marked unavailable when its required metric is absent rather than querying a
    guessed identifier and interpreting the empty result as normal operation.
    """

    service_names = [str(member.get("service_name", "")) for member in members]
    selector = _report_selector(service_names)
    node_label, node_selector = _report_node_selector(members, host_label_override)
    # Keep source identity labels on host metrics. Historical node IDs can change
    # when PMM agents are re-registered; node_name, instance, and job let the
    # report map those old series without discarding their provenance.
    host_labels = "node_id,node_name,instance,job,agent_id,node_type"
    disk_labels = f"{host_labels},device"
    service_metric_labels = "service_name,node_id,node_name,instance,job,agent_id,service_id"
    definitions = {
        "cpu_used_percent": (
            ("node_cpu_seconds_total",),
            lambda names: f'100 * (1 - avg by ({host_labels}) (rate({names[0]}{{mode="idle",{node_selector}}}[5m])))',
        ),
        "memory_used_percent": (
            ("node_memory_MemAvailable_bytes", "node_memory_MemTotal_bytes"),
            lambda names: f'100 * (1 - max by ({host_labels}) ({names[0]}{{{node_selector}}}) / max by ({host_labels}) ({names[1]}{{{node_selector}}}))',
        ),
        "root_filesystem_used_percent": (
            ("node_filesystem_avail_bytes", "node_filesystem_size_bytes"),
            lambda names: (
                f'100 * (1 - max by ({host_labels},mountpoint,device,fstype) '
                f'({names[0]}{{mountpoint="/",{node_selector}}}) / '
                f'max by ({host_labels},mountpoint,device,fstype) ({names[1]}{{mountpoint="/",{node_selector}}}))'
            ),
        ),
        "connection_used_percent": (
            ("mongodb_connections",),
            lambda names: (
                f'100 * sum by ({service_metric_labels}) ({names[0]}{{state="current",{selector}}}) '
                f'/ (sum by ({service_metric_labels}) ({names[0]}{{state="current",{selector}}}) '
                f'+ sum by ({service_metric_labels}) ({names[0]}{{state="available",{selector}}}))'
            ),
        ),
        "wiredtiger_dirty_page_percent": (
            (
                "mongodb_ss_wt_cache_tracked_dirty_bytes_in_the_cache",
                "mongodb_mongod_wiredtiger_cache_max_bytes",
            ),
            lambda names: (
                f'100 * max by ({service_metric_labels}) ({names[0]}{{{selector}}}) / '
                f'max by ({service_metric_labels}) ({names[1]}{{{selector}}})'
            ),
        ),
        "wiredtiger_cache_used_percent": (
            ("mongodb_mongod_wiredtiger_cache_bytes", "mongodb_mongod_wiredtiger_cache_max_bytes"),
            lambda names: f'100 * max by ({service_metric_labels}) ({names[0]}{{{selector}}}) / max by ({service_metric_labels}) ({names[1]}{{{selector}}})',
        ),
        "wiredtiger_evictions_per_second": (
            ("mongodb_mongod_wiredtiger_cache_evicted_total",),
            lambda names: f'sum by ({service_metric_labels}) (rate({names[0]}{{{selector}}}[5m]))',
        ),
        "disk_iops": (
            ("node_disk_reads_completed_total", "node_disk_writes_completed_total"),
            # Keep each block device visible. The report summarizes the highest
            # per-device sampled rate instead of summing stacked dm/physical
            # devices and double-counting the same I/O.
            lambda names: f'max by ({disk_labels}) (rate({names[0]}{{{node_selector}}}[5m]) + rate({names[1]}{{{node_selector}}}[5m]))',
        ),
        "disk_io_latency_seconds": (
            ("node_disk_reads_completed_total", "node_disk_writes_completed_total", "node_disk_read_time_seconds_total", "node_disk_write_time_seconds_total"),
            lambda names: f'(sum by ({disk_labels}) (rate({names[2]}{{{node_selector}}}[5m]) + rate({names[3]}{{{node_selector}}}[5m]))) / (sum by ({disk_labels}) (rate({names[0]}{{{node_selector}}}[5m]) + rate({names[1]}{{{node_selector}}}[5m])))',
        ),
        "disk_read_iops": (
            ("node_disk_reads_completed_total",),
            lambda names: f'max by ({disk_labels}) (rate({names[0]}{{{node_selector}}}[5m]))',
        ),
        "disk_write_iops": (
            ("node_disk_writes_completed_total",),
            lambda names: f'max by ({disk_labels}) (rate({names[0]}{{{node_selector}}}[5m]))',
        ),
        "disk_read_latency_seconds": (
            ("node_disk_reads_completed_total", "node_disk_read_time_seconds_total"),
            lambda names: f'sum by ({disk_labels}) (rate({names[1]}{{{node_selector}}}[5m])) / sum by ({disk_labels}) (rate({names[0]}{{{node_selector}}}[5m]))',
        ),
        "disk_write_latency_seconds": (
            ("node_disk_writes_completed_total", "node_disk_write_time_seconds_total"),
            lambda names: f'sum by ({disk_labels}) (rate({names[1]}{{{node_selector}}}[5m])) / sum by ({disk_labels}) (rate({names[0]}{{{node_selector}}}[5m]))',
        ),
        "operation_latency_seconds": (
            ("mongodb_mongod_op_latencies_latency_total", "mongodb_mongod_op_latencies_ops_total"),
            # Counters are in microseconds and operation counts; summing both
            # counters over operation types yields a correctly weighted mean.
            lambda names: f'sum by ({service_metric_labels}) (rate({names[0]}{{{selector}}}[5m])) / sum by ({service_metric_labels}) (rate({names[1]}{{{selector}}}[5m])) / 1000000',
        ),
        "replication_lag_seconds": (
            ("mongodb_mongod_replset_member_replication_lag",),
            lambda names: f'avg by ({service_metric_labels},member_host,member_name) ({names[0]}{{{selector}}})',
        ),
        "operations_per_second": (
            ("mongodb_op_counters_total",),
            lambda names: f'sum by ({service_metric_labels}) (rate({names[0]}{{{selector}}}[5m]))',
        ),
    }
    candidates, expression = definitions[metric]
    query_selector = node_selector if metric in _HOST_METRICS else selector
    if not query_selector:
        return None, None
    if not all(candidate in available_metrics for candidate in candidates):
        return " / ".join(candidates), None
    return " / ".join(candidates), expression(candidates)


def _report_role_query(
    available_metrics: set[str], service_names: list[str]
) -> tuple[str | None, str | None]:
    """Return the known MongoDB exporter member-state query when available."""

    source = "mongodb_mongod_replset_my_state"
    selector = _report_selector(service_names)
    if source not in available_metrics or not selector:
        return None, None
    return source, f"{source}{{{selector}}}"


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
    step: str = "",
    cluster: str = "",
    replication_set: str = "",
) -> dict[str, object]:
    """Gera uma avaliação de performance por cluster/replica set a partir do PMM.

    O resultado segue a estrutura de uma avaliação Percona Managed Services:
    condição observada, avaliação, relevância ao cliente e foco recomendado.
    Ele é uma investigação somente leitura e não substitui o health check padrão.
    Métricas indisponíveis permanecem explícitas no relatório.
    Sem ``step`` explícito, a resolução é escolhida pela duração, pelo scrape
    interval e pelo orçamento de pontos; picos de workload podem receber um
    drill-down de curta duração e passo mais fino quando houver amostras.

    Esta ferramenta coleta e retorna os dados PMM estruturados. No fluxo do
    agente, cada solicitação de relatório só está concluída depois que o cliente
    cria e verifica um Google Docs novo, copiando o modelo visual aprovado com
    o conector Google Drive/Docs; não entregue somente este resultado estruturado.
    """

    resolved_id, client = _resolve_client(client_id)
    services = _filter_services(
        client.mongodb_services(), cluster=cluster, replication_set=replication_set
    )
    if not services:
        raise ValueError("Nenhum serviço MongoDB corresponde ao cluster ou replica set informado.")
    period_start, period_end = resolve_utc_window(period_hours, end)
    query_start = period_start
    retention: str | None = None
    retention_lookup = getattr(client, "prometheus_retention", None)
    if callable(retention_lookup):
        try:
            retention = retention_lookup()
        except PMMAPIError:
            # Keep PMM-specific retention behavior visible through returned samples.
            retention = None
    query_start, retention_limited = bound_start_to_retention(
        period_start, period_end, retention
    )

    catalog, _ = _client_catalog(resolved_id, client)
    available_metrics = {
        str(name) for name in catalog.get("metrics", {}).get("prometheus", [])
    }
    reports: list[dict[str, object]] = []
    for group in service_groups(services):
        members = list(group["members"])
        service_names = [str(member.get("service_name", "")) for member in members]
        scrape_interval: str | None = None
        scrape_interval_lookup = getattr(client, "prometheus_scrape_interval", None)
        if callable(scrape_interval_lookup):
            try:
                scrape_interval = scrape_interval_lookup(members)
            except PMMAPIError:
                # Some PMM deployments do not expose the Prometheus targets API.
                # Keep the duration-based step and actual returned sample counts visible.
                scrape_interval = None
        report_step = select_report_step(period_hours, scrape_interval, step or None)
        metric_summaries: dict[str, dict[str, dict[str, object]]] = {}
        metric_observations: dict[str, dict[str, list[dict[str, Any]]]] = {}
        metric_sources: dict[str, str | None] = {}
        metric_queries: dict[str, dict[str, object]] = {}
        workload_query: str | None = None
        workload_result: dict[str, Any] | None = None
        expected_report_samples = expected_range_samples(
            query_start, period_end, parse_prometheus_duration(report_step)
        )
        host_probe_selector = _report_node_selector(members)[1]
        boot_result: dict[str, Any] | None = None
        disk_identity_result: dict[str, Any] | None = None
        host_probe_details: dict[str, Any] = {}
        for metric_name, destination in (
            ("node_boot_time_seconds", "boot_time"),
            ("node_disk_info", "block_devices"),
        ):
            if metric_name not in available_metrics or not host_probe_selector:
                host_probe_details[destination] = {"status": "metric_not_in_catalog"}
                continue
            try:
                result = client.prometheus_query(
                    f"{metric_name}{{{host_probe_selector}}}", max_results=5000
                )
                if destination == "boot_time":
                    boot_result = result
                else:
                    disk_identity_result = result
                host_probe_details[destination] = {
                    "status": "available" if result.get("data", {}).get("result") else "no_series_returned",
                    "query": result.get("query"),
                    "series": len(result.get("data", {}).get("result", [])),
                }
            except PMMAPIError as exc:
                host_probe_details[destination] = {"status": "query_failed", "reason": str(exc)}
        host_topology = infer_host_topology(members, boot_result, disk_identity_result)
        host_topology["probe_details"] = host_probe_details
        report_metrics = set(_REPORT_THRESHOLDS) | {
            "replication_lag_seconds", "operations_per_second", "memory_used_percent",
            "wiredtiger_cache_used_percent", "wiredtiger_evictions_per_second",
            "disk_iops", "disk_io_latency_seconds", "operation_latency_seconds",
            "disk_read_iops", "disk_write_iops", "disk_read_latency_seconds",
            "disk_write_latency_seconds",
        }
        host_metrics = _HOST_METRICS
        for metric in sorted(report_metrics):
            source, query = _report_metric_query(available_metrics, metric, members)
            metric_sources[metric] = source
            primary_host_label = _report_node_selector(members)[0] if metric in host_metrics else None
            metric_queries[metric] = {
                "report_query": query,
                "report_step": report_step,
                "primary_host_label": primary_host_label,
                "fallback_query": None,
                "selected_host_identity_by_member": {},
                "status": "planned" if query else "metric_not_in_catalog_or_identity_unavailable",
                "unavailable_reason": None if query else (
                    f"Required PMM series absent from the metric catalog: {', '.join(name for name in str(source or '').split(' / ') if name and name not in available_metrics)}"
                    if source and any(name not in available_metrics for name in str(source).split(" / "))
                    else "Required host identity labels were not present in PMM inventory."
                ),
                "source_metrics": str(source).split(" / ") if source else [],
                "promql": query,
                "unit": {
                    "cpu_used_percent": "%", "memory_used_percent": "%",
                    "root_filesystem_used_percent": "%", "disk_iops": "ops/s per device",
                    "disk_read_iops": "read ops/s per device", "disk_write_iops": "write ops/s per device",
                    "disk_io_latency_seconds": "s/op per device",
                    "disk_read_latency_seconds": "s/read per device",
                    "disk_write_latency_seconds": "s/write per device",
                    "wiredtiger_cache_used_percent": "%", "wiredtiger_evictions_per_second": "evictions/s",
                    "operation_latency_seconds": "s/op",
                }.get(metric),
                "aggregation_semantics": {
                    "cpu_used_percent": "100 minus average idle-core rate over 5m; grouped by exporter identity",
                    "memory_used_percent": "100 minus MemAvailable/MemTotal; grouped by exporter identity",
                    "root_filesystem_used_percent": "100 minus available/size for mountpoint /; grouped by exporter identity and filesystem labels",
                    "disk_iops": "highest read-plus-write IOPS of an individual device; stacked devices are not summed",
                    "disk_read_iops": "highest read IOPS of an individual device",
                    "disk_write_iops": "highest write IOPS of an individual device",
                    "disk_io_latency_seconds": "read-plus-write time divided by completions per individual device; no value when completion rate is zero",
                    "disk_read_latency_seconds": "read time divided by read completions per individual device; no value when completion rate is zero",
                    "disk_write_latency_seconds": "write time divided by write completions per individual device; no value when completion rate is zero",
                    "wiredtiger_cache_used_percent": "cache bytes / maximum cache bytes",
                    "wiredtiger_dirty_page_percent": "tracked dirty cache bytes / maximum cache bytes",
                    "wiredtiger_evictions_per_second": "rate of cumulative eviction counter over 5m",
                    "operation_latency_seconds": "rate(latency microseconds total) / rate(operation count), weighted across operation types then converted to seconds/op",
                    "operations_per_second": "sum of counter rates over 5m by PMM service/exporter identity",
                }.get(metric),
            }
            if query is None:
                metric_summaries[metric] = {
                    name: {"status": "unavailable", "samples": 0, "latest": None, "mean": None, "maximum": None}
                    for name in service_names if name
                }
                metric_observations[metric] = {name: [] for name in service_names if name}
                add_metric_coverage(
                    metric_summaries[metric], metric_observations[metric],
                    query_start, period_end, parse_prometheus_duration(report_step),
                )
                continue
            try:
                result = client.prometheus_query(query, query_start, period_end, report_step, 5000)
            except PMMAPIError as exc:
                # PMM/exporter releases differ in label schemas as well as metric
                # names. Preserve a report with an explicit telemetry gap instead
                # of losing every other independent metric family.
                metric_summaries[metric] = {
                    name: {"status": "unavailable", "samples": 0, "latest": None, "mean": None, "maximum": None}
                    for name in service_names if name
                }
                metric_observations[metric] = {name: [] for name in service_names if name}
                metric_sources[metric] = None
                metric_queries[metric]["status"] = "query_failed"
                metric_queries[metric]["unavailable_reason"] = str(exc)
                add_metric_coverage(
                    metric_summaries[metric], metric_observations[metric],
                    query_start, period_end, parse_prometheus_duration(report_step),
                )
                continue
            result_data = result.get("data", {})
            result_series = result_data.get("result", []) if isinstance(result_data, dict) else []
            metric_queries[metric]["status"] = "available" if result_series else "no_series_returned"
            metric_queries[metric]["series_returned"] = len(result_series) if isinstance(result_series, list) else 0
            metric_queries[metric]["query_truncated"] = bool(result_data.get("truncated")) if isinstance(result_data, dict) else False
            primary_observations = extract_member_observations(result, members, metric)
            # A node_id query can return a recent subset while historical samples
            # remain labeled only by node_name. Retry on sparse coverage as well
            # as an empty result, then select the richer series per inventoried
            # member instead of silently treating a partial non-empty result as
            # complete history.
            if (metric in host_metrics and primary_host_label == "node_id"
                    and (not result_series or needs_host_label_fallback(
                        primary_observations, expected_report_samples
                    ))):
                fallback_source, fallback_query = _report_metric_query(
                    available_metrics, metric, members, host_label_override="node_name"
                )
                metric_queries[metric]["fallback_query"] = fallback_query
                if fallback_query:
                    try:
                        fallback_result = client.prometheus_query(
                            fallback_query, query_start, period_end, report_step, 5000
                        )
                        fallback_data = fallback_result.get("data", {})
                        fallback_series = fallback_data.get("result", []) if isinstance(fallback_data, dict) else []
                        metric_queries[metric]["fallback_series_returned"] = len(fallback_series) if isinstance(fallback_series, list) else 0
                        metric_queries[metric]["fallback_query_truncated"] = bool(fallback_data.get("truncated")) if isinstance(fallback_data, dict) else False
                        if fallback_series:
                            fallback_observations = extract_member_observations(
                                fallback_result, members, metric
                            )
                            metric_observations[metric], identities = prefer_more_complete_observations(
                                primary_observations, fallback_observations, metric
                            )
                            metric_queries[metric]["selected_host_identity_by_member"] = identities
                            if any("fallback" in identity for identity in identities.values()):
                                metric_sources[metric] = f"{fallback_source} (node_name/address coverage fallback)"
                    except PMMAPIError as exc:
                        metric_queries[metric]["fallback_query_error"] = str(exc)
            if not metric_observations.get(metric):
                metric_observations[metric] = primary_observations
            if metric not in host_metrics:
                metric_observations[metric] = primary_observations
            elif not metric_queries[metric]["selected_host_identity_by_member"]:
                metric_queries[metric]["selected_host_identity_by_member"] = {
                    name: primary_host_label or "service_name" for name in service_names if name
                }
            metric_summaries[metric] = summarize_member_observations(metric_observations[metric], metric)
            add_metric_coverage(
                metric_summaries[metric], metric_observations[metric],
                query_start, period_end, parse_prometheus_duration(report_step),
            )
            metric_queries[metric]["returned_samples_by_member"] = {
                name: summary.get("samples", 0)
                for name, summary in metric_summaries[metric].items()
            }
            if metric == "operations_per_second":
                workload_query = query
                workload_result = result
        apply_host_topology(
            metric_summaries, metric_observations, host_topology,
            query_start, period_end,
            parse_prometheus_duration(report_step),
        )
        for metric, query_detail in metric_queries.items():
            summaries = metric_summaries.get(metric, {})
            if query_detail.get("status") == "available" and not any(
                summary.get("status") == "ok" for summary in summaries.values()
            ):
                query_detail["status"] = "no_member_samples_mapped"
                query_detail["unavailable_reason"] = (
                    "Prometheus returned series, but none matched the inventoried service/node identities."
                )
            elif query_detail.get("status") == "no_series_returned":
                query_detail["unavailable_reason"] = "PromQL ran successfully, but returned no series in the requested window."
            query_detail["returned_samples_by_member"] = {
                name: summary.get("samples", 0) for name, summary in summaries.items()
            }
            query_detail["coverage_by_member"] = {
                name: summary.get("coverage") for name, summary in summaries.items()
            }
        role_source, role_query = _report_role_query(available_metrics, service_names)
        role_result: dict[str, Any] = {}
        if role_query is not None:
            try:
                role_result = client.prometheus_query(role_query, max_results=5000)
            except PMMAPIError:
                # Role telemetry is independent of performance metric families.
                # Preserve the report, but the consistency check will report the gap.
                role_source = None
        member_roles = summarize_member_role_series(role_result, members)
        role_range_observations: dict[str, list[dict[str, Any]]] = {name: [] for name in service_names if name}
        if role_source is not None and role_query is not None:
            role_range_query = role_query
            try:
                role_range_result = client.prometheus_query(
                    role_range_query, query_start, period_end, report_step, 5000
                )
                role_range_observations = extract_member_observations(
                    role_range_result, members, "replica_set_member_state"
                )
            except PMMAPIError:
                # Current role is not evidence of a historical event's role.
                role_range_observations = {name: [] for name in service_names if name}
        report = build_replica_set_report(
            str(group["cluster"]), str(group["replica_set"]), members, metric_summaries,
            _REPORT_THRESHOLDS, member_roles, role_metric_available=role_source is not None,
        )
        report["period"] = {
            "start": period_start,
            "query_start": query_start,
            "end": period_end,
            "step": report_step,
            "scrape_interval": scrape_interval or "unknown",
            "retention": retention or "unknown",
        }
        report["metric_sources"] = metric_sources
        report["metric_sources"]["replica_set_member_state"] = role_source
        report["metric_query_details"] = metric_queries
        report["host_topology"] = host_topology
        report["sections"]["resources_and_capacity"]["host_topology"] = host_topology
        # Keep the raw query_range observations alongside aggregate statistics so
        # later consumers can audit each timestamp, label and member/host mapping.
        report["time_series_observations"] = metric_observations
        report["time_series_observations"]["replica_set_member_state"] = role_range_observations
        report["peak_drilldown_observations"] = {"cpu_used_percent": {}}
        report["limitations"].append(
            "Range maxima are sampled maxima of returned report-range observations, not guaranteed maxima for the entire period or instantaneous peaks; per-member coverage reports missing expected evaluation points."
        )
        if host_topology.get("status") != "available":
            report["limitations"].append(
                "Physical host sharing could not be verified from PMM node-exporter boot-time and block-device identity labels; host metrics are scoped to PMM-inventoried nodes and must not be assumed to represent independent physical hosts."
            )
        for metric, detail in metric_queries.items():
            if detail.get("unavailable_reason"):
                report["limitations"].append(
                    f"{metric} unavailable: {detail['unavailable_reason']}"
                )
        if retention_limited:
            report["limitations"].append(
                f"The requested start predates the configured Prometheus retention of {retention}; range queries begin at query_start."
            )
        if workload_query and workload_result is not None:
            peaks = detect_member_workload_peaks(workload_result, members)
            scrape_seconds = None
            try:
                scrape_seconds = parse_prometheus_duration(scrape_interval) if scrape_interval else None
            except ValueError:
                scrape_seconds = None
            step_seconds = parse_prometheus_duration(report_step)
            alignment_tolerance = max(step_seconds, scrape_seconds or step_seconds) / 2
            events = correlate_workload_peaks(
                peaks,
                {metric: data for metric, data in metric_observations.items()
                 if metric != "replica_set_member_state"},
                members, alignment_tolerance_seconds=alignment_tolerance,
                host_identity_by_member=host_topology.get("by_member", {}),
            )
            for event in events:
                repl = event["correlated_metrics"].get("replication_lag_seconds")
                event_timestamp = datetime.fromisoformat(str(event["peak_timestamp"].replace("Z", "+00:00"))).timestamp()
                role_samples = [
                    item for item in role_range_observations.get(str(event["service_name"]), [])
                    if abs(float(item["timestamp_epoch"]) - event_timestamp) <= alignment_tolerance
                ]
                historical_role = historical_role_at_timestamp(
                    role_samples, event_timestamp, alignment_tolerance
                )
                event["member_role_at_peak"] = historical_role
                if historical_role["status"] == "primary" and repl is not None:
                    repl.update({
                        "status": "not_applicable_primary",
                        "event_samples": 0,
                        "event_mean": None,
                        "event_min": None,
                        "event_max": None,
                        "baseline_samples": 0,
                        "baseline_mean": None,
                        "expected_baseline_samples": 0,
                        "missing_baseline_samples": 0,
                        "baseline_coverage_percent": None,
                        "change_percent": None,
                        "timestamps": [],
                        "assessment": "The exporter lag series describes member lag relative to the replica-set primary; a primary's self-lag is not a valid replication-lag indicator.",
                    })
                    event["missing_evidence"].append("replication_lag_seconds (not semantically applicable to primary)")
                elif historical_role["status"] != "secondary" and repl is not None:
                    repl.update({
                        "status": "role_unknown_at_peak",
                        "event_samples": 0,
                        "event_mean": None,
                        "event_min": None,
                        "event_max": None,
                        "assessment": "Historical member role is unavailable or conflicting at this peak, so replication lag cannot be interpreted for this event.",
                    })
                    event["missing_evidence"].append("replication_lag_seconds (member role unavailable or conflicting at peak)")
            report["sections"]["workload_peak_analysis_and_metric_correlation"]["events"] = events
            report["sections"]["workload_peak_analysis_and_metric_correlation"]["window"] = {
                "before_seconds": 300, "after_seconds": 300,
                "alignment_tolerance_seconds": alignment_tolerance,
                "baseline_seconds": 900,
            }
            drilldown_step = select_peak_drilldown_step(period_hours, scrape_interval)
            drilldowns: list[dict[str, object]] = []
            # Keep the existing finer drill-down limited to each member's sampled
            # maximum. Correlation of the other distinct events reuses the
            # one-minute series already collected above.
            drilldown_peaks: list[dict[str, object]] = []
            seen_peak_members: set[str] = set()
            for candidate in peaks:
                candidate_service = str(candidate["service_name"])
                if candidate_service in seen_peak_members:
                    continue
                seen_peak_members.add(candidate_service)
                drilldown_peaks.append(candidate)
            start_at = datetime.fromisoformat(query_start.replace("Z", "+00:00"))
            end_at = datetime.fromisoformat(period_end.replace("Z", "+00:00"))
            member_by_name = {
                str(member.get("service_name", "")): member for member in members
            }
            for peak in drilldown_peaks:
                member_name = str(peak["service_name"])
                member = member_by_name.get(member_name)
                if member is None:
                    continue
                peak_at = datetime.fromtimestamp(float(peak["timestamp"]), UTC)
                # Keep the short query grid anchored to the workload-peak
                # timestamp. The previous +/-7m30s bounds shifted 1m samples by
                # 30s, making exact event observations harder to compare.
                detail_start = max(start_at, peak_at - timedelta(minutes=7))
                detail_end = min(end_at, peak_at + timedelta(minutes=8))
                if detail_start >= detail_end:
                    continue
                detail_source, detail_query = _report_metric_query(
                    available_metrics, "operations_per_second", [member]
                )
                if detail_query is None:
                    continue
                try:
                    detail_result = client.prometheus_query(
                        detail_query,
                        detail_start.astimezone(UTC).isoformat().replace("+00:00", "Z"),
                        detail_end.astimezone(UTC).isoformat().replace("+00:00", "Z"),
                        drilldown_step,
                        5000,
                    )
                except PMMAPIError:
                    drilldowns.append({
                        "service_name": member_name,
                        "status": "unavailable",
                        "peak_time": peak_at.isoformat().replace("+00:00", "Z"),
                        "step": drilldown_step,
                        "samples": 0,
                        "metric_source": detail_source,
                    })
                    continue
                detail_summary = summarize_member_series(
                    detail_result, [member], "operations_per_second"
                ).get(member_name, {})
                detail_ops = extract_member_observations(
                    detail_result, [member], "operations_per_second"
                ).get(member_name, [])
                cpu_detail_rows: list[dict[str, Any]] = []
                cpu_detail_query_samples = 0
                cpu_detail_source: str | None = None
                cpu_detail_query: str | None = None
                cpu_detail_fallback_query: str | None = None
                cpu_identity = "node_id"
                cpu_summary = metric_summaries.get("cpu_used_percent", {}).get(member_name, {})
                if parse_prometheus_duration(drilldown_step) < parse_prometheus_duration(report_step):
                    cpu_detail_source, cpu_detail_query = _report_metric_query(
                        available_metrics, "cpu_used_percent", [member]
                    )
                    cpu_initial_query = cpu_detail_query
                    if cpu_detail_query:
                        try:
                            cpu_detail_result = client.prometheus_query(
                                cpu_detail_query,
                                detail_start.astimezone(UTC).isoformat().replace("+00:00", "Z"),
                                detail_end.astimezone(UTC).isoformat().replace("+00:00", "Z"),
                                drilldown_step,
                                5000,
                            )
                            cpu_detail_rows_by_member = extract_member_observations(
                                cpu_detail_result, members, "cpu_used_percent"
                            )
                            cpu_detail_rows = cpu_detail_rows_by_member.get(member_name, [])
                            detail_expected = expected_range_samples(
                                detail_start.astimezone(UTC).isoformat().replace("+00:00", "Z"),
                                detail_end.astimezone(UTC).isoformat().replace("+00:00", "Z"),
                                parse_prometheus_duration(drilldown_step),
                            )
                            if (not cpu_detail_result.get("data", {}).get("result")
                                    or needs_host_label_fallback(
                                        {member_name: cpu_detail_rows}, detail_expected
                                    )) and _report_node_selector([member])[0] == "node_id":
                                fallback_cpu_source, fallback_cpu_query = _report_metric_query(
                                    available_metrics, "cpu_used_percent", [member],
                                    host_label_override="node_name",
                                )
                                if fallback_cpu_query:
                                    cpu_detail_fallback_query = fallback_cpu_query
                                    try:
                                        fallback_cpu_result = client.prometheus_query(
                                            fallback_cpu_query,
                                            detail_start.astimezone(UTC).isoformat().replace("+00:00", "Z"),
                                            detail_end.astimezone(UTC).isoformat().replace("+00:00", "Z"),
                                            drilldown_step,
                                            5000,
                                        )
                                        fallback_cpu_rows = extract_member_observations(
                                            fallback_cpu_result, members, "cpu_used_percent"
                                        )
                                        selected_cpu_rows, cpu_identities = prefer_more_complete_observations(
                                            {member_name: cpu_detail_rows}, fallback_cpu_rows,
                                            "cpu_used_percent",
                                        )
                                        cpu_detail_rows = selected_cpu_rows.get(member_name, [])
                                        cpu_identity = cpu_identities.get(member_name, "node_id")
                                        if cpu_detail_rows and cpu_identity == "node_name/address":
                                            cpu_detail_query = fallback_cpu_query
                                            cpu_detail_source = (
                                                f"{fallback_cpu_source} (node_name/address coverage fallback)"
                                            )
                                    except PMMAPIError:
                                        pass
                            cpu_detail_query_samples = len(cpu_detail_rows)
                            cpu_source_step = parse_prometheus_duration(drilldown_step)
                            scrape_seconds_for_detail = None
                            try:
                                scrape_seconds_for_detail = (
                                    parse_prometheus_duration(scrape_interval) if scrape_interval else None
                                )
                            except ValueError:
                                pass
                            detail_tolerance = max(
                                cpu_source_step, scrape_seconds_for_detail or cpu_source_step
                            ) / 2
                            peak_window_start = float(peak["timestamp"]) - 300
                            peak_window_end = float(peak["timestamp"]) + 300
                            detail_op_epochs = [
                                float(row["timestamp_epoch"]) for row in detail_ops
                                if peak_window_start <= float(row["timestamp_epoch"]) <= peak_window_end
                            ]
                            cpu_detail_rows = [
                                row for row in cpu_detail_rows
                                if peak_window_start <= float(row["timestamp_epoch"]) <= peak_window_end
                                and detail_op_epochs
                                and min(abs(float(row["timestamp_epoch"]) - op_epoch)
                                        for op_epoch in detail_op_epochs) <= detail_tolerance
                            ]
                            cpu_host_group = host_topology.get("by_member", {}).get(member_name, {})
                            for row in cpu_detail_rows:
                                row["query_step"] = drilldown_step
                                row["query_source"] = "peak_drilldown"
                                row["host_group_id"] = cpu_host_group.get("host_group_id")
                                row["physical_host_id"] = cpu_host_group.get("host_group_id")
                                row["host_shared"] = bool(cpu_host_group.get("shared_host"))
                                row["host_members"] = cpu_host_group.get("host_members", [member_name])
                                row["measurement_scope"] = (
                                    "shared_host" if cpu_host_group.get("shared_host")
                                    else "PMM-inventoried node; physical-host uniqueness unverified"
                                )
                                row["physical_identity_confidence"] = cpu_host_group.get("physical_identity_confidence")
                            label_example = cpu_detail_rows[0].get("labels", {}) if cpu_detail_rows else {}
                            if cpu_detail_rows:
                                host_label_name = "node_name" if "node_name" in label_example else "node_id"
                                host_label_value = label_example.get(host_label_name, "unknown")
                                cpu_host_identity = (
                                    f"{host_label_name}={host_label_value} mapped to inventory "
                                    f"node_id={member.get('node_id') or 'unknown'}"
                                )
                            else:
                                cpu_host_identity = (
                                    f"inventory node_id={member.get('node_id') or 'unknown'}"
                                )
                            event_row = next((item for item in events
                                              if item["service_name"] == member_name
                                              and abs(float(item["peak_timestamp_epoch"])
                                                      - float(peak["timestamp"])) <= 1), None)
                            if event_row is None:
                                # Correlation currently emits an ISO timestamp but
                                # retains the epoch only in the source peak list.
                                event_row = next((item for item in events
                                                  if item["service_name"] == member_name
                                                  and item["peak_timestamp"] == peak_at.isoformat().replace("+00:00", "Z")), None)
                            if event_row is not None:
                                detail_evidence = attach_peak_detail_evidence(
                                    event_row, "cpu_used_percent", cpu_detail_rows,
                                    cpu_summary, drilldown_step, cpu_host_identity,
                                    expected_host_id=str(member.get("node_id") or member.get("node_name") or member.get("address") or "") or None,
                                    workload_peak_timestamp=str(detail_summary.get("maximum_timestamp") or "") or None,
                                )
                            else:
                                detail_evidence = None
                            report["peak_drilldown_observations"]["cpu_used_percent"][member_name] = cpu_detail_rows
                            metric_queries["cpu_used_percent"].setdefault("peak_drilldown", {})[member_name] = {
                                "initial_query": cpu_initial_query,
                                "query": cpu_detail_query,
                                "fallback_query_attempted": cpu_detail_fallback_query,
                                "source": cpu_detail_source,
                                "step": drilldown_step,
                                "selected_host_identity": cpu_identity,
                                "query_returned_samples": cpu_detail_query_samples,
                                "aligned_peak_window_samples": len(cpu_detail_rows),
                            }
                        except PMMAPIError:
                            cpu_detail_rows = []
                            detail_evidence = None
                    else:
                        detail_evidence = None
                else:
                    detail_evidence = None
                drilldowns.append({
                    "service_name": member_name,
                    "status": "available" if detail_summary.get("samples", 0) else "unavailable",
                    "peak_time": peak_at.isoformat().replace("+00:00", "Z"),
                    "detected_peak_ops_per_second": peak["value"],
                    "peak_detection_samples": peak["samples"],
                    "window": {
                        "start": detail_start.astimezone(UTC).isoformat().replace("+00:00", "Z"),
                        "end": detail_end.astimezone(UTC).isoformat().replace("+00:00", "Z"),
                    },
                    "step": drilldown_step,
                    "samples": detail_summary.get("samples", 0),
                    "mean_ops_per_second": detail_summary.get("mean"),
                    "maximum_ops_per_second": detail_summary.get("maximum"),
                    "maximum_ops_per_second_timestamp": detail_summary.get("maximum_timestamp"),
                    "metric_source": detail_source,
                    "cpu_peak_detail": detail_evidence,
                })
            report["sections"]["workload_and_concurrency"]["peak_drilldown"] = {
                "status": "available" if any(item["status"] == "available" for item in drilldowns)
                else "unavailable" if peaks else "no_detected_peak",
                "trigger": "one sampled maximum per member receives the existing finer drill-down; up to two additional distinct candidates are correlated using the one-minute range series",
                "step": drilldown_step,
                "windows": drilldowns,
            }
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
