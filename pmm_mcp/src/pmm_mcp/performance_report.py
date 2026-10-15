"""Helpers for a customer-facing MongoDB performance review.

The module deliberately contains no PMM I/O.  It turns the inventory and
read-only Prometheus responses collected by the MCP tool into a stable report
contract, keeping missing telemetry visible instead of treating it as healthy.
"""

from __future__ import annotations

import math
from typing import Any


def _number(value: object) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def service_groups(services: list[dict[str, Any]]) -> list[dict[str, object]]:
    """Group inventory services into reportable cluster/replica-set scopes."""

    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for service in services:
        cluster = str(service.get("cluster") or "ungrouped")
        replica_set = str(service.get("replication_set") or "ungrouped")
        groups.setdefault((cluster, replica_set), []).append(service)
    return [
        {"cluster": cluster, "replica_set": replica_set, "members": members}
        for (cluster, replica_set), members in sorted(groups.items())
    ]


def summarize_member_series(
    result: dict[str, Any], members: list[dict[str, Any]]
) -> dict[str, dict[str, object]]:
    """Summarize range-vector data by PMM service name without interpolating gaps."""

    known = {str(member.get("service_name", "")) for member in members}
    summary = {
        name: {"status": "unavailable", "samples": 0, "latest": None, "mean": None, "maximum": None}
        for name in known
        if name
    }
    data = result.get("data", {})
    series = data.get("result", []) if isinstance(data, dict) else []
    if not isinstance(series, list):
        return summary
    collected: dict[str, list[tuple[float, float]]] = {}
    for item in series:
        if not isinstance(item, dict):
            continue
        labels = item.get("metric", {})
        if not isinstance(labels, dict):
            continue
        name = str(labels.get("service_name") or labels.get("node_name") or "")
        if name not in summary:
            continue
        for point in item.get("values", []):
            if not isinstance(point, (list, tuple)) or len(point) < 2:
                continue
            timestamp, value = _number(point[0]), _number(point[1])
            if timestamp is not None and value is not None:
                collected.setdefault(name, []).append((timestamp, value))
    for name, points in collected.items():
        points.sort()
        values = [point[1] for point in points]
        summary[name] = {
            "status": "ok",
            "samples": len(values),
            "latest": values[-1],
            "mean": sum(values) / len(values),
            "maximum": max(values),
        }
    return summary


def make_assessment(
    members: list[dict[str, Any]], metrics: dict[str, dict[str, dict[str, object]]],
    thresholds: dict[str, float],
) -> dict[str, object]:
    """Create conservative, evidence-led assessment text and actionable findings."""

    findings: list[dict[str, object]] = []
    for metric, threshold in (("cpu_used_percent", thresholds["cpu_used_percent"]),
                              ("root_filesystem_used_percent", thresholds["root_filesystem_used_percent"]),
                              ("connection_used_percent", thresholds["connection_used_percent"]),
                              ("wiredtiger_dirty_page_percent", thresholds["wiredtiger_dirty_page_percent"])):
        values = metrics.get(metric, {})
        affected = [name for name, value in values.items()
                    if value.get("status") == "ok" and isinstance(value.get("maximum"), float)
                    and value["maximum"] > threshold]
        if affected:
            title = {
                "cpu_used_percent": "CPU utilization requires review",
                "root_filesystem_used_percent": "Root filesystem capacity requires review",
                "connection_used_percent": "Connection capacity requires review",
                "wiredtiger_dirty_page_percent": "WiredTiger dirty-page pressure requires review",
            }[metric]
            findings.append({
                "finding": title,
                "observed_condition": f"{len(affected)}/{len(members)} member(s) exceeded the observational {threshold:g}% threshold.",
                "affected_members": affected,
                "percona_assessment": "The condition merits targeted review; this report does not establish customer impact or replace an approved health-check policy.",
                "recommended_focus": "Correlate the time series with workload, operational events, and the applicable capacity or service objective.",
            })
    missing = [metric for metric, values in metrics.items() if not any(v.get("status") == "ok" for v in values.values())]
    observed = "Available PMM samples did not show a configured observational threshold crossing."
    if findings:
        observed = "Available PMM samples contain conditions that require focused review."
    if missing:
        observed += " Some metric families were unavailable and are explicitly listed in limitations."
    return {
        "current_observed_condition": observed,
        "percona_assessment": "Assessment is based on available PMM inventory and read-only time series; it is not a declaration of service health.",
        "customer_relevance": "Validate findings against workload peaks, SLOs, planned growth, and failover reserve before changing capacity or topology.",
        "percona_suggested_focus": "Prioritize threshold crossings and telemetry gaps, then continue trending workload and replication behavior.",
        "findings": findings,
        "unavailable_metric_families": missing,
    }


def build_replica_set_report(
    cluster: str,
    replica_set: str,
    members: list[dict[str, Any]],
    metrics: dict[str, dict[str, dict[str, object]]],
    thresholds: dict[str, float],
) -> dict[str, object]:
    """Return one structured Percona Managed Services-style assessment."""

    roles = {"primary": 0, "secondary": 0, "unknown": 0}
    topology_members = []
    for member in members:
        name = str(member.get("service_name", ""))
        state = "unknown"
        # Inventory currently does not guarantee member state; preserve this fact.
        topology_members.append({
            "service_name": name,
            "node_name": member.get("node_name", ""),
            "version": member.get("version", ""),
            "agent_status": [agent.get("status", "") for agent in member.get("agents", [])],
            "role_state": state,
        })
    return {
        "report_type": "mongodb_cluster_performance_review",
        "title": "Percona Managed Services Assessment",
        "scope": {"cluster": cluster, "replica_set": replica_set, "member_count": len(members)},
        "executive_summary": make_assessment(members, metrics, thresholds),
        "topology": {
            "summary": f"Replica set {replica_set} contains {len(members)} inventoried member(s).",
            "exceptions": ["PMM inventory does not provide a reliable primary/secondary state in this collection."],
            "percona_assessment": "Topology sizing or consistency cannot be concluded from inventory alone; compare the expected member roles and failure-domain design with the current deployment.",
            "members": topology_members,
        },
        "sections": {
            "resources_and_capacity": {"metrics": {key: value for key, value in metrics.items() if key in {"cpu_used_percent", "root_filesystem_used_percent"}}},
            "mongodb_storage_engine_health": {"percona_assessment": "Evaluate dirty-page behavior with cache eviction, queues, tickets, and latency; cache occupancy alone is not pressure.", "metrics": {key: value for key, value in metrics.items() if key == "wiredtiger_dirty_page_percent"}},
            "workload_and_concurrency": {"percona_assessment": "Interpret connection and operation trends alongside workload shape and latency; absent telemetry is not evidence of headroom.", "metrics": {key: value for key, value in metrics.items() if key in {"connection_used_percent", "operations_per_second"}}},
            "replication_and_cluster_health": {"percona_assessment": "Review replica lag by member and distinguish intentionally delayed members before classifying lag as instability.", "metrics": {key: value for key, value in metrics.items() if key == "replication_lag_seconds"}},
            "operational_events_and_alerts": {"status": "not_available", "percona_assessment": "PMM alert-event history is not collected by this MCP report. Correlate recurring alerts separately with the metrics above."},
        },
        "limitations": [
            "Range summaries use available PMM samples only; gaps are not interpolated.",
            "Observational thresholds are report parameters, not the standard health-check policy.",
            "Host/member detail is retained in this structured appendix field rather than treated as a topology conclusion.",
        ],
    }
