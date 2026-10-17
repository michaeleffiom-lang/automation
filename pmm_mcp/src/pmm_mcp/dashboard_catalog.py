"""Cached inventory of metrics used by PMM Grafana dashboards."""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .pmm_api import PMMClient


_SELECTOR_METRIC = re.compile(
    r"(?<![A-Za-z0-9_:$/])([A-Za-z_:][A-Za-z0-9_:]*)(?=\s*(?:\{|\[))"
)
_CATALOG_SCHEMA_VERSION = "0.2.0"
_BARE_METRIC = re.compile(r"(?<![A-Za-z0-9_:$/])([A-Za-z_:][A-Za-z0-9_:]*)")
_PROMQL_KEYWORDS = {
    "and",
    "bool",
    "by",
    "group_left",
    "group_right",
    "ignoring",
    "on",
    "or",
    "unless",
    "without",
}
_PROMQL_FUNCTIONS = {
    "abs",
    "absent",
    "avg",
    "avg_over_time",
    "bottomk",
    "ceil",
    "changes",
    "clamp",
    "clamp_max",
    "clamp_min",
    "count",
    "count_values",
    "day_of_month",
    "day_of_week",
    "days_in_month",
    "delta",
    "deriv",
    "exp",
    "floor",
    "histogram_avg",
    "histogram_count",
    "histogram_fraction",
    "histogram_quantile",
    "histogram_sum",
    "histogram_stddev",
    "histogram_stdvar",
    "holt_winters",
    "hour",
    "increase",
    "irate",
    "last_over_time",
    "label_join",
    "label_replace",
    "ln",
    "log10",
    "log2",
    "max",
    "max_over_time",
    "min",
    "min_over_time",
    "month",
    "predict_linear",
    "present_over_time",
    "quantile",
    "quantile_over_time",
    "rate",
    "resets",
    "round",
    "scalar",
    "sgn",
    "sort",
    "sort_desc",
    "sqrt",
    "sum",
    "sum_over_time",
    "stddev",
    "stddev_over_time",
    "stdvar",
    "stdvar_over_time",
    "time",
    "timestamp",
    "topk",
    "vector",
    "year",
}


def _without_strings(expression: str) -> str:
    return re.sub(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'', " ", expression)


def extract_metric_names(expression: str) -> list[str]:
    """Extract likely Prometheus metric identifiers from a dashboard query."""

    expression = _without_strings(expression)
    names = set(_SELECTOR_METRIC.findall(expression))
    scan_expression = re.sub(r"\{[^{}]*\}", " ", expression)
    scan_expression = re.sub(
        r"\b(?:by|on|ignoring|without|group_left|group_right)\s*\([^)]*\)",
        " ",
        scan_expression,
    )
    for match in _BARE_METRIC.finditer(scan_expression):
        name = match.group(1)
        if name.lower() in _PROMQL_KEYWORDS or name.lower() in _PROMQL_FUNCTIONS:
            continue
        if match.start() and expression[match.start() - 1] == "$":
            continue
        after = expression[match.end() :].lstrip()
        if after.startswith("("):
            continue
        # Names inside label/grouping lists are not metric identifiers.
        prefix = scan_expression[: match.start()].rstrip()
        if prefix.endswith(("by", "on", "ignoring", "without", "group_left", "group_right")):
            continue
        names.add(name)
    return sorted(names)


def _flatten_panels(panels: list[object]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for raw in panels:
        if not isinstance(raw, dict):
            continue
        result.append(raw)
        nested = raw.get("panels")
        if isinstance(nested, list):
            result.extend(_flatten_panels(nested))
    return result


def _dashboard_category(item: dict[str, Any]) -> str | None:
    tags = {str(tag).strip().lower() for tag in item.get("tags", []) if tag}
    folder = str(item.get("folderTitle", "")).strip().lower()
    if "mongodb" in tags or "mongodb_ha" in tags or folder == "mongodb":
        return "mongodb"
    if "os" in tags or folder == "os":
        return "hardware"
    return None


def _cache_directory() -> Path:
    configured = os.getenv("PMM_CATALOG_CACHE_DIR")
    if configured:
        return Path(configured).expanduser()
    return Path(__file__).resolve().parents[2] / ".pmm-cache"


def cache_path(client: PMMClient) -> Path:
    identity = f"{client.config.client_id}|{client.config.base_url}".encode()
    digest = hashlib.sha256(identity).hexdigest()[:12]
    safe_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", client.config.client_id).strip("._") or "client"
    return _cache_directory() / f"{safe_id}-{digest}.json"


def _now() -> datetime:
    return datetime.now(UTC)


def _dashboard_record(client: PMMClient, index_item: dict[str, Any], category: str) -> dict[str, Any]:
    uid = str(index_item.get("uid", ""))
    dashboard = client.grafana_dashboard(uid)
    charts: list[dict[str, Any]] = []
    usage: dict[str, list[dict[str, Any]]] = {}
    for panel in _flatten_panels(dashboard.get("panels", [])):
        targets = panel.get("targets")
        if not isinstance(targets, list):
            continue
        queries: list[dict[str, Any]] = []
        panel_metrics: set[str] = set()
        for target in targets:
            if not isinstance(target, dict):
                continue
            expression = target.get("expr")
            if not isinstance(expression, str) or not expression.strip():
                continue
            metrics = extract_metric_names(expression)
            panel_metrics.update(metrics)
            queries.append(
                {
                    "ref_id": str(target.get("refId", "")),
                    "legend_format": str(target.get("legendFormat", "")),
                    "expr": expression,
                    "metrics": metrics,
                }
            )
        if not queries:
            continue
        panel_id = str(panel.get("id", ""))
        panel_record = {
            "panel_id": panel_id,
            "title": str(panel.get("title", "")),
            "type": str(panel.get("type", "")),
            "metrics": sorted(panel_metrics),
            "queries": queries,
        }
        charts.append(panel_record)
        for metric in panel_metrics:
            usage.setdefault(metric, []).append(
                {
                    "dashboard_uid": uid,
                    "dashboard_title": str(dashboard.get("title", index_item.get("title", ""))),
                    "panel_id": panel_id,
                    "panel_title": str(panel.get("title", "")),
                }
            )
    metric_names = sorted(usage)
    return {
        "uid": uid,
        "title": str(dashboard.get("title", index_item.get("title", ""))),
        "folder": str(index_item.get("folderTitle", "")),
        "tags": [str(tag) for tag in index_item.get("tags", []) if tag],
        "category": category,
        "version": dashboard.get("version"),
        "chart_count": len(charts),
        "metric_count": len(metric_names),
        "metrics": metric_names,
        "charts": charts,
    }


def build_catalog(client: PMMClient) -> dict[str, Any]:
    """Read selected dashboards and all current metric names once."""

    selected = [
        (item, _dashboard_category(item))
        for item in client.grafana_dashboards()
        if _dashboard_category(item)
    ]
    categories: dict[str, list[dict[str, Any]]] = {"hardware": [], "mongodb": []}
    for item, category in selected:
        assert category is not None
        categories[category].append(_dashboard_record(client, item, category))

    dashboard_metrics = {
        category: sorted(
            {metric for dashboard in dashboards for metric in dashboard["metrics"]}
        )
        for category, dashboards in categories.items()
    }
    prometheus_metrics = client.metric_names(limit=50000)
    prometheus_metric_set = set(prometheus_metrics)
    dashboard_available_metrics = {
        category: sorted(name for name in names if name in prometheus_metric_set)
        for category, names in dashboard_metrics.items()
    }
    for dashboards in categories.values():
        for dashboard in dashboards:
            available = [
                name for name in dashboard.get("metrics", []) if name in prometheus_metric_set
            ]
            dashboard["available_metrics"] = available
            dashboard["available_metric_count"] = len(available)
    return {
        "schema_version": _CATALOG_SCHEMA_VERSION,
        "client_id": client.config.client_id,
        "base_url": client.config.base_url,
        "generated_at": _now().isoformat(),
        "source": {
            "dashboard_search": f"{client.config.base_url}/graph/api/search?type=dash-db",
            "dashboard_count": len(selected),
            "metric_names_collected": True,
        },
        "metrics": {
            "prometheus": prometheus_metrics,
            "dashboard": dashboard_metrics,
            "dashboard_available": dashboard_available_metrics,
        },
        "dashboards": categories,
    }


def load_catalog(client: PMMClient, refresh: bool = False) -> tuple[dict[str, Any], bool]:
    """Load a fresh-enough catalog, refreshing it only when necessary."""

    path = cache_path(client)
    ttl = max(0, int(os.getenv("PMM_CATALOG_TTL_SECONDS", "86400")))
    if not refresh and path.exists():
        try:
            catalog = json.loads(path.read_text(encoding="utf-8"))
            if catalog.get("schema_version") != _CATALOG_SCHEMA_VERSION:
                raise ValueError("catálogo antigo")
            generated = datetime.fromisoformat(str(catalog.get("generated_at", "")))
            if (_now() - generated).total_seconds() <= ttl:
                return catalog, True
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            pass

    catalog = build_catalog(client)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(catalog, ensure_ascii=False, indent=2), encoding="utf-8")
    return catalog, False


def catalog_summary(catalog: dict[str, Any], cached: bool) -> dict[str, Any]:
    dashboards = catalog.get("dashboards", {})
    metrics = catalog.get("metrics", {})
    return {
        "schema_version": catalog.get("schema_version", _CATALOG_SCHEMA_VERSION),
        "client_id": catalog.get("client_id", ""),
        "base_url": catalog.get("base_url", ""),
        "generated_at": catalog.get("generated_at", ""),
        "cached": cached,
        "dashboard_counts": {
            category: len(items) if isinstance(items, list) else 0
            for category, items in dashboards.items()
        },
        "metric_counts": {
            "prometheus": len(metrics.get("prometheus", [])) if isinstance(metrics, dict) else 0,
            **{
                category: len(items) if isinstance(items, list) else 0
                for category, items in metrics.get("dashboard_available", {}).items()
            },
        },
        "cache_file": str(cache_path_from_catalog(catalog)),
    }


def cache_path_from_catalog(catalog: dict[str, Any]) -> Path:
    identity = f"{catalog.get('client_id', '')}|{catalog.get('base_url', '')}".encode()
    digest = hashlib.sha256(identity).hexdigest()[:12]
    safe_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(catalog.get("client_id", "client"))).strip("._") or "client"
    return _cache_directory() / f"{safe_id}-{digest}.json"
