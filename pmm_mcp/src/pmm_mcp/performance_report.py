"""Helpers for a customer-facing MongoDB performance review.

The module deliberately contains no PMM I/O.  It turns the inventory and
read-only Prometheus responses collected by the MCP tool into a stable report
contract, keeping missing telemetry visible instead of treating it as healthy.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime
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


_REPLICA_STATE_NAMES = {
    0: "startup",
    1: "primary",
    2: "secondary",
    3: "recovering",
    5: "startup2",
    6: "unknown",
    7: "arbiter",
    8: "down",
    9: "rollback",
    10: "removed",
}


def summarize_member_role_series(
    result: dict[str, Any], members: list[dict[str, Any]]
) -> dict[str, dict[str, object]]:
    """Map MongoDB's current replica-state gauge to inventoried PMM services."""

    known = {str(member.get("service_name", "")) for member in members}
    summary = {
        name: {
            "status": "unavailable",
            "role_state": "unknown",
            "state_code": None,
            "samples": 0,
            "observed_states": [],
            "observed_at": None,
            "observed_at_epoch": None,
            "conflict": False,
        }
        for name in known
        if name
    }
    data = result.get("data", {})
    series = data.get("result", []) if isinstance(data, dict) else []
    if not isinstance(series, list):
        return summary

    collected: dict[str, list[tuple[float, int]]] = {}
    for item in series:
        if not isinstance(item, dict):
            continue
        labels = item.get("metric", {})
        if not isinstance(labels, dict):
            continue
        name = str(labels.get("service_name") or labels.get("node_name") or "")
        if name not in summary:
            continue
        raw_points: list[Any] = []
        if isinstance(item.get("values"), list):
            raw_points.extend(item["values"])
        if isinstance(item.get("value"), (list, tuple)):
            raw_points.append(item["value"])
        for point in raw_points:
            if not isinstance(point, (list, tuple)) or len(point) < 2:
                continue
            timestamp, raw_state = _number(point[0]), _number(point[1])
            if timestamp is None or raw_state is None or not raw_state.is_integer():
                continue
            collected.setdefault(name, []).append((timestamp, int(raw_state)))

    for name, points in collected.items():
        points.sort()
        latest_timestamp = points[-1][0]
        latest_codes = {code for timestamp, code in points if timestamp == latest_timestamp}
        conflict = len(latest_codes) != 1
        state_code = next(iter(latest_codes)) if not conflict else None
        role_state = _REPLICA_STATE_NAMES.get(state_code, "unknown") if state_code is not None else "unknown"
        try:
            observed_at = datetime.fromtimestamp(latest_timestamp, UTC).isoformat().replace("+00:00", "Z")
        except (OverflowError, OSError, ValueError):
            observed_at = None
        summary[name] = {
            "status": "conflicting" if conflict else "ok" if role_state != "unknown" else "unknown_state",
            "role_state": role_state,
            "state_code": state_code,
            "samples": len(points),
            "observed_states": sorted({_REPLICA_STATE_NAMES.get(code, "unknown") for _, code in points}),
            "observed_at": observed_at,
            "observed_at_epoch": latest_timestamp,
            "conflict": conflict,
        }
    return summary


def check_report_consistency(
    members: list[dict[str, Any]],
    role_summary: dict[str, dict[str, object]],
    role_metric_available: bool,
) -> dict[str, object]:
    """Check report role coverage and the single-primary replica-set invariant."""

    names = [str(member.get("service_name", "")) for member in members if member.get("service_name")]
    observed = {
        name: role_summary.get(name, {})
        for name in names
    }
    complete = bool(names) and all(
        item.get("status") == "ok" and item.get("role_state") != "unknown"
        for item in observed.values()
    )
    warnings: list[str] = []
    if not role_metric_available:
        coverage_status = "unavailable"
        primary_status = "not_checked"
        warnings.append("Replica-set member roles could not be checked because mongodb_mongod_replset_my_state is unavailable.")
    else:
        missing = [name for name, item in observed.items() if item.get("status") != "ok"]
        if missing:
            coverage_status = "warning"
            warnings.append("Role-state telemetry is missing or conflicting for: " + ", ".join(missing) + ".")
        else:
            coverage_status = "passed"

        timestamps = [
            _number(item.get("observed_at_epoch"))
            for item in observed.values()
            if item.get("observed_at_epoch") is not None
        ]
        # Prometheus instant vectors may use slightly different scrape times.
        synchronized = bool(names) and len(timestamps) == len(names) and max(timestamps) - min(timestamps) <= 60
        if not complete or not synchronized:
            primary_status = "not_checked"
            if complete and not synchronized:
                warnings.append("Role-state samples are more than 60 seconds apart; primary-count consistency was not evaluated.")
        else:
            primary_count = sum(item.get("role_state") == "primary" for item in observed.values())
            primary_status = "passed" if primary_count == 1 else "warning"
            if primary_count != 1:
                warnings.append(f"Role-state telemetry reports {primary_count} primary members; expected exactly one.")

    return {
        "status": "not_available" if not role_metric_available else "warning" if warnings else "passed",
        "role_metric": "mongodb_mongod_replset_my_state",
        "member_count": len(names),
        "members_with_role": sum(item.get("status") == "ok" for item in observed.values()),
        "checks": {
            "role_state_coverage": coverage_status,
            "single_primary": primary_status,
        },
        "warnings": warnings,
    }


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
    member_roles: dict[str, dict[str, object]] | None = None,
    role_metric_available: bool = False,
) -> dict[str, object]:
    """Return one structured Percona Managed Services-style assessment."""

    member_roles = member_roles or {}
    topology_members = []
    for member in members:
        name = str(member.get("service_name", ""))
        role = member_roles.get(name, {})
        topology_members.append({
            "service_name": name,
            "node_name": member.get("node_name", ""),
            "version": member.get("version", ""),
            "agent_status": [agent.get("status", "") for agent in member.get("agents", [])],
            "role_state": role.get("role_state", "unknown"),
            "role_state_code": role.get("state_code"),
            "role_state_status": role.get("status", "unavailable"),
            "role_state_observed_at": role.get("observed_at"),
        })
    consistency = check_report_consistency(members, member_roles, role_metric_available)
    exceptions = list(consistency["warnings"])
    if not exceptions:
        exceptions = ["No role-state inconsistencies were found in the available PMM snapshot."]
    observed_roles = [str(member["role_state"]) for member in topology_members]
    secondary_count = observed_roles.count("secondary")
    topology_summary = f"Replica set {replica_set} contains {len(members)} inventoried member(s)."
    if role_metric_available and consistency["checks"]["single_primary"] == "passed":
        topology_summary += f" The PMM role-state snapshot shows one primary and {secondary_count} secondary member(s)."
    return {
        "report_type": "mongodb_cluster_performance_review",
        "title": "Percona Managed Services Assessment",
        "scope": {"cluster": cluster, "replica_set": replica_set, "member_count": len(members)},
        "executive_summary": make_assessment(members, metrics, thresholds),
        "topology": {
            "summary": topology_summary,
            "exceptions": exceptions,
            "percona_assessment": (
                "The PMM member-state snapshot is consistent with exactly one primary. It is point-in-time evidence, not a health declaration or a history of role changes."
                if role_metric_available and consistency["checks"]["single_primary"] == "passed"
                else "Member roles must be validated from available PMM state telemetry; unknown or inconsistent roles are called out above."
            ),
            "members": topology_members,
        },
        "consistency_check": consistency,
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
            "Replica-set member states are point-in-time PMM observations and do not provide a history of elections or role changes.",
        ],
    }
