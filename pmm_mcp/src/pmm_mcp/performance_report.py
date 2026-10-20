"""Helpers for a customer-facing MongoDB performance review.

The module deliberately contains no PMM I/O.  It turns the inventory and
read-only Prometheus responses collected by the MCP tool into a stable report
contract, keeping missing telemetry visible instead of treating it as healthy.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime
from typing import Any

from pmm_mcp.report_validation import check_report_consistency, validate_performance_report


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
    result: dict[str, Any], members: list[dict[str, Any]], metric: str = "unknown"
) -> dict[str, dict[str, object]]:
    """Summarize range-vector data by PMM service name without interpolating gaps."""
    observations = extract_member_observations(result, members, "unknown")
    return summarize_member_observations(observations, metric)


def summarize_member_observations(
    observations: dict[str, list[dict[str, Any]]],
    metric: str = "unknown",
) -> dict[str, dict[str, object]]:
    """Summarize mapped observations while retaining the timestamp of the sampled max."""

    summary: dict[str, dict[str, object]] = {}
    for name, rows in observations.items():
        # One evaluation timestamp is one sample. This also prevents a fallback
        # query from double-counting overlapping node_id and node_name series.
        ordered = _select_timestamp_observations(rows, metric)
        if not ordered:
            summary[name] = {
                "status": "unavailable", "samples": 0, "latest": None, "mean": None,
                "maximum": None, "maximum_timestamp": None,
                "maximum_basis": "maximum of returned report-range samples",
            }
            continue
        values = [float(row["value"]) for row in ordered]
        maximum_row = max(ordered, key=lambda row: (float(row["value"]), float(row["timestamp_epoch"])))
        summary[name] = {
            "status": "ok",
            "samples": len(ordered),
            "latest": values[-1],
            "mean": sum(values) / len(values),
            "maximum": float(maximum_row["value"]),
            "maximum_timestamp": maximum_row["timestamp"],
            "maximum_timestamp_epoch": float(maximum_row["timestamp_epoch"]),
            "maximum_labels": maximum_row.get("labels", {}),
            "maximum_source_node_id": maximum_row.get("source_node_id"),
            "maximum_source_instance": maximum_row.get("source_instance"),
            "maximum_source_job": maximum_row.get("source_job"),
            "maximum_basis": "maximum of returned report-range samples",
            "shared_host": bool(maximum_row.get("host_shared", False)),
            "host_members": maximum_row.get("host_members", []),
            "host_group_id": maximum_row.get("host_group_id"),
            "measurement_scope": maximum_row.get("measurement_scope"),
            "host_metric_source_member": maximum_row.get("host_metric_source_member"),
            "physical_identity_confidence": maximum_row.get("physical_identity_confidence"),
        }
    return summary


def expected_range_samples(start: str, end: str, step_seconds: float) -> int:
    """Count evaluation timestamps on the requested inclusive query_range grid."""

    if step_seconds <= 0:
        return 0
    start_epoch = datetime.fromisoformat(start.replace("Z", "+00:00")).timestamp()
    end_epoch = datetime.fromisoformat(end.replace("Z", "+00:00")).timestamp()
    if end_epoch < start_epoch:
        return 0
    return int(math.floor((end_epoch - start_epoch) / step_seconds + 1e-9)) + 1


def add_metric_coverage(
    summaries: dict[str, dict[str, object]],
    observations: dict[str, list[dict[str, Any]]],
    start: str,
    end: str,
    step_seconds: float,
) -> None:
    """Attach returned-versus-expected sample coverage without filling gaps."""

    expected = expected_range_samples(start, end, step_seconds)
    for service, summary in summaries.items():
        rows = observations.get(service, [])
        timestamps = sorted({
            float(row["timestamp_epoch"])
            for row in rows
            if _number(row.get("timestamp_epoch")) is not None
        })
        returned = len(timestamps)
        summary["coverage"] = {
            "basis": "report_range_query_samples",
            "expected_samples": expected,
            "returned_samples": returned,
            "missing_samples": max(0, expected - returned),
            "coverage_percent": returned / expected * 100 if expected else None,
            "step_seconds": step_seconds,
            "observed_start": datetime.fromtimestamp(timestamps[0], UTC).isoformat().replace("+00:00", "Z") if timestamps else None,
            "observed_end": datetime.fromtimestamp(timestamps[-1], UTC).isoformat().replace("+00:00", "Z") if timestamps else None,
        }


def needs_host_label_fallback(
    observations: dict[str, list[dict[str, Any]]],
    expected_samples: int,
    minimum_coverage: float = 0.9,
) -> bool:
    """Retry node_name/address when a non-empty node_id result is materially sparse."""

    if expected_samples <= 0:
        return False
    for rows in observations.values():
        timestamps = {float(row["timestamp_epoch"]) for row in rows
                      if _number(row.get("timestamp_epoch")) is not None}
        if len(timestamps) / expected_samples < minimum_coverage:
            return True
    return False


def prefer_more_complete_observations(
    primary: dict[str, list[dict[str, Any]]],
    fallback: dict[str, list[dict[str, Any]]],
    metric: str | None = None,
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, str]]:
    """Merge historical name-mapped identities without duplicating evaluation points."""

    selected: dict[str, list[dict[str, Any]]] = {}
    identities: dict[str, str] = {}
    for service in sorted(set(primary) | set(fallback)):
        primary_rows = primary.get(service, [])
        fallback_rows = fallback.get(service, [])
        primary_count = len({float(row["timestamp_epoch"]) for row in primary_rows})
        fallback_count = len({float(row["timestamp_epoch"]) for row in fallback_rows})
        current_ids = {
            str(row.get("labels", {}).get("node_id"))
            for row in primary_rows
            if row.get("labels", {}).get("node_id")
        }
        by_key: dict[tuple[float, str], dict[str, Any]] = {}
        for row in primary_rows + fallback_rows:
            timestamp = float(row["timestamp_epoch"])
            device = str(row.get("labels", {}).get("device") or "") if metric in _DISK_METRIC_NAMES else ""
            key = (timestamp, device)
            candidate_is_primary = row in primary_rows
            existing = by_key.get(key)
            if existing is None:
                by_key[key] = row
                continue
            existing_is_primary = existing in primary_rows
            candidate_current = str(row.get("labels", {}).get("node_id") or "") in current_ids
            existing_current = str(existing.get("labels", {}).get("node_id") or "") in current_ids
            if (candidate_is_primary, candidate_current) > (existing_is_primary, existing_current):
                by_key[key] = row
        selected[service] = sorted(by_key.values(), key=lambda row: (
            float(row["timestamp_epoch"]), str(row.get("labels", {}).get("device") or "")
        ))
        identities[service] = (
            "node_id + node_name/address historical fallback"
            if fallback_count > primary_count else "node_id"
        )
    return selected, identities


def attach_peak_detail_evidence(
    event: dict[str, Any],
    metric: str,
    detail_observations: list[dict[str, Any]],
    summary: dict[str, object],
    detail_step: str,
    host_identity: str,
    expected_host_id: str | None = None,
    workload_peak_timestamp: str | None = None,
) -> dict[str, Any]:
    """Add aligned high-resolution evidence while keeping range coverage separate."""

    rows = sorted((row for row in detail_observations
                   if expected_host_id is None or str(row.get("host_id") or "") == expected_host_id),
                  key=lambda row: float(row["timestamp_epoch"]))
    detail_peak = max(rows, key=lambda row: (float(row["value"]), float(row["timestamp_epoch"]))) if rows else None
    aligned_to_workload_peak = next(
        (row for row in rows if workload_peak_timestamp and row.get("timestamp") == workload_peak_timestamp),
        None,
    )
    range_max = _number(summary.get("maximum"))
    peak_value = _number(detail_peak.get("value")) if detail_peak else None
    if detail_peak is None:
        relation = "no_aligned_detail_observations"
    elif range_max is None:
        relation = "range_summary_unavailable"
    elif peak_value is not None and peak_value > range_max:
        relation = "detail_exceeds_sampled_range_maximum"
    else:
        relation = "detail_does_not_exceed_sampled_range_maximum"
    event.setdefault("correlated_metrics", {}).setdefault(metric, {})["peak_drilldown"] = {
        "status": "available" if rows else "unavailable",
        "step": detail_step,
        "host_identity": host_identity,
        "host_id": expected_host_id,
        "shared_host": any(bool(row.get("host_shared")) for row in rows),
        "host_members": sorted({member for row in rows for member in row.get("host_members", [])}),
        "samples": len(rows),
        "maximum": peak_value,
        "maximum_timestamp": detail_peak.get("timestamp") if detail_peak else None,
        "workload_peak_timestamp": workload_peak_timestamp,
        "at_workload_peak": ({
            "timestamp": aligned_to_workload_peak["timestamp"],
            "value": aligned_to_workload_peak["value"],
            "unit": aligned_to_workload_peak.get("unit"),
        } if aligned_to_workload_peak else None),
        "range_summary_maximum": range_max,
        "range_summary_maximum_timestamp": summary.get("maximum_timestamp"),
        "range_summary_samples": summary.get("samples", 0),
        "range_summary_coverage": summary.get("coverage"),
        "comparison": relation,
        "observations": [{
            "timestamp": row["timestamp"], "value": row["value"], "unit": row.get("unit"),
            "host_id": row.get("host_id"), "node_name": row.get("node_name"),
            "labels": row.get("labels", {}),
        } for row in rows],
        "assessment": (
            "These aligned observations use the same CPU metric definition and inventory-mapped host. "
            "They supplement the report-range sampled maximum and do not imply complete period coverage or causation."
        ),
    }
    return event["correlated_metrics"][metric]["peak_drilldown"]


_METRIC_UNITS = {
    "operations_per_second": "ops/s",
    "cpu_used_percent": "%",
    "memory_used_percent": "%",
    "root_filesystem_used_percent": "%",
    "wiredtiger_dirty_page_percent": "%",
    "wiredtiger_cache_used_percent": "%",
    "wiredtiger_evictions_per_second": "evictions/s",
    "disk_iops": "ops/s",
    "disk_io_latency_seconds": "s/op",
    "disk_read_iops": "ops/s",
    "disk_write_iops": "ops/s",
    "disk_read_latency_seconds": "s/op",
    "disk_write_latency_seconds": "s/op",
    "connection_used_percent": "%",
    "operation_latency_seconds": "s/op",
    "replication_lag_seconds": "s",
    "replica_set_member_state": "state code",
}

_HOST_METRIC_NAMES = {
    "cpu_used_percent", "memory_used_percent", "root_filesystem_used_percent",
    "disk_iops", "disk_io_latency_seconds", "disk_read_iops", "disk_write_iops",
    "disk_read_latency_seconds", "disk_write_latency_seconds",
}
_DISK_METRIC_NAMES = {
    "disk_iops", "disk_io_latency_seconds", "disk_read_iops", "disk_write_iops",
    "disk_read_latency_seconds", "disk_write_latency_seconds",
}


def infer_host_topology(
    members: list[dict[str, Any]],
    boot_result: dict[str, Any] | None,
    disk_result: dict[str, Any] | None,
) -> dict[str, Any]:
    """Infer shared node-exporter views only from explicit IDs or container signatures.

    PMM node IDs identify registered nodes, not necessarily physical hosts. For
    container nodes, a matching kernel boot time and complete block-device
    identity set is evidence that exporters share the same host kernel view.
    Otherwise the physical-host relationship remains unverified.
    """

    services = [str(member.get("service_name", "")) for member in members if member.get("service_name")]
    member_by_service = {str(member.get("service_name", "")): member for member in members}
    node_id_to_services: dict[str, list[str]] = {}
    node_name_to_services: dict[str, list[str]] = {}
    for member in members:
        name = str(member.get("service_name", ""))
        if not name:
            continue
        node_id = str(member.get("node_id") or "")
        node_name = str(member.get("node_name") or member.get("address") or "")
        if node_id:
            node_id_to_services.setdefault(node_id, []).append(name)
        if node_name:
            node_name_to_services.setdefault(node_name, []).append(name)

    def mapped_services(labels: dict[str, Any]) -> list[str]:
        node_id = str(labels.get("node_id") or "")
        matched = node_id_to_services.get(node_id, [])
        if matched:
            return matched
        node_name = str(labels.get("node_name") or "")
        return node_name_to_services.get(node_name, [])

    metadata: dict[str, dict[str, Any]] = {
        service: {"boot_time": None, "node_type": "", "physical_ids": set(), "devices": set()}
        for service in services
    }
    explicit_host_labels = ("machine_id", "system_uuid", "host_uuid", "cloud_instance_id")
    for result in (boot_result or {},):
        data = result.get("data", {})
        series = data.get("result", []) if isinstance(data, dict) else []
        if not isinstance(series, list):
            continue
        for item in series:
            if not isinstance(item, dict) or not isinstance(item.get("metric"), dict):
                continue
            labels = item["metric"]
            points = item.get("values", [])
            point = item.get("value")
            if isinstance(points, list) and points:
                point = points[-1]
            boot_time = _number(point[1]) if isinstance(point, (list, tuple)) and len(point) > 1 else None
            for service in mapped_services(labels):
                row = metadata[service]
                if boot_time is not None:
                    row["boot_time"] = boot_time
                row["node_type"] = str(labels.get("node_type") or row["node_type"])
                row["physical_ids"].update(
                    (label, str(labels[label])) for label in explicit_host_labels if labels.get(label)
                )

    data = (disk_result or {}).get("data", {})
    series = data.get("result", []) if isinstance(data, dict) else []
    if isinstance(series, list):
        for item in series:
            if not isinstance(item, dict) or not isinstance(item.get("metric"), dict):
                continue
            labels = item["metric"]
            device = str(labels.get("device") or "")
            major = str(labels.get("major") or "")
            minor = str(labels.get("minor") or "")
            if not (device and major and minor):
                continue
            for service in mapped_services(labels):
                metadata[service]["devices"].add((device, major, minor))

    parent = {service: service for service in services}

    def root(service: str) -> str:
        while parent[service] != service:
            parent[service] = parent[parent[service]]
            service = parent[service]
        return service

    def join(left: str, right: str) -> None:
        a, b = root(left), root(right)
        if a != b:
            parent[max(a, b)] = min(a, b)

    # Two MongoDB services registered to the same PMM node or node name share
    # the same logical host identity even if a different physical ID is absent.
    for mapping in (node_id_to_services, node_name_to_services):
        for mapped in mapping.values():
            for service in mapped[1:]:
                join(mapped[0], service)

    for index, left in enumerate(services):
        left_meta = metadata[left]
        for right in services[index + 1:]:
            right_meta = metadata[right]
            shared_explicit_id = bool(left_meta["physical_ids"] & right_meta["physical_ids"])
            both_containers = left_meta["node_type"].lower() in {"container", "docker", "podman", "containerd"} and \
                right_meta["node_type"].lower() in {"container", "docker", "podman", "containerd"}
            same_kernel_signature = (
                both_containers
                and left_meta["boot_time"] is not None
                and left_meta["boot_time"] == right_meta["boot_time"]
                and bool(left_meta["devices"])
                and left_meta["devices"] == right_meta["devices"]
            )
            if shared_explicit_id or same_kernel_signature:
                join(left, right)

    grouped: dict[str, list[str]] = {}
    for service in services:
        grouped.setdefault(root(service), []).append(service)
    groups: list[dict[str, Any]] = []
    by_member: dict[str, dict[str, Any]] = {}
    for index, group_members in enumerate(sorted((sorted(group) for group in grouped.values()), key=lambda group: group[0]), 1):
        shared = len(group_members) > 1
        evidence: list[str] = []
        methods: set[str] = set()
        for service in group_members:
            meta = metadata[service]
            if meta["physical_ids"]:
                methods.add("explicit_physical_id")
            if meta["node_type"].lower() in {"container", "docker", "podman", "containerd"}:
                methods.add("container_kernel_signature")
        if shared:
            if "explicit_physical_id" in methods:
                evidence.append("matching explicit physical-host identifier label")
            if "container_kernel_signature" in methods:
                evidence.append("container targets report the same kernel boot time and block-device identities")
            if len({str(member_by_service[service].get("node_id") or "") for service in group_members}) < len(group_members):
                evidence.append("multiple MongoDB services share one PMM inventory node")
            if len({str(member_by_service[service].get("node_name") or member_by_service[service].get("address") or "") for service in group_members}) < len(group_members):
                evidence.append("multiple MongoDB services share one node name/address")
        group_id = f"shared-host-{index}" if shared else f"node:{member_by_service[group_members[0]].get('node_id') or group_members[0]}"
        group = {
            "host_group_id": group_id,
            "members": group_members,
            "shared": shared,
            "identity_method": sorted(methods) or (["shared_inventory_node"] if shared else ["inventory_node_id"]),
            "physical_identity_confidence": "evidence_based_shared" if shared else "physical_host_unverified",
            "evidence": evidence,
            "metric_sources": {},
        }
        groups.append(group)
        for service in group_members:
            by_member[service] = {
                "host_group_id": group_id,
                "shared_host": shared,
                "host_members": list(group_members) if shared else [service],
                "physical_identity_confidence": group["physical_identity_confidence"],
                "identity_method": group["identity_method"],
                "representative_service_by_metric": {},
            }
    return {
        "status": "available" if boot_result or disk_result else "unavailable",
        "identity_probe_metrics": [
            metric for metric, result in (("node_boot_time_seconds", boot_result), ("node_disk_info", disk_result))
            if result is not None
        ],
        "groups": groups,
        "by_member": by_member,
    }


def _select_timestamp_observations(
    rows: list[dict[str, Any]], metric: str,
) -> list[dict[str, Any]]:
    """Select one provenance-preserving row per evaluation timestamp."""

    by_timestamp: dict[float, list[dict[str, Any]]] = {}
    for row in rows:
        timestamp = _number(row.get("timestamp_epoch"))
        value = _number(row.get("value"))
        if timestamp is not None and value is not None:
            by_timestamp.setdefault(timestamp, []).append(row)
    selected: list[dict[str, Any]] = []
    for timestamp, candidates in sorted(by_timestamp.items()):
        if metric in _DISK_METRIC_NAMES:
            row = max(candidates, key=lambda item: float(item["value"]))
        else:
            row = max(candidates, key=lambda item: (
                bool(item.get("source_matches_inventory_node_id")),
                bool(item.get("labels", {}).get("node_type")),
                str(item.get("labels", {}).get("instance") or ""),
            ))
        selected.append({**row, "timestamp_epoch": timestamp, "value": float(row["value"])})
    return selected


def apply_host_topology(
    summaries: dict[str, dict[str, dict[str, object]]],
    observations: dict[str, dict[str, list[dict[str, Any]]]],
    host_topology: dict[str, Any],
    start: str,
    end: str,
    step_seconds: float,
) -> None:
    """Deduplicate shared host series and expose their scope in summaries."""

    by_member = host_topology.get("by_member", {})
    for metric in _HOST_METRIC_NAMES:
        service_rows = observations.get(metric, {})
        metric_summaries = summaries.get(metric, {})
        for group in host_topology.get("groups", []):
            group_members = list(group.get("members", []))
            if not group_members:
                continue
            if group.get("shared"):
                representative = max(
                    group_members,
                    key=lambda service: (
                        len({float(row["timestamp_epoch"]) for row in service_rows.get(service, [])}),
                        service,
                    ),
                )
                selected_rows = _select_timestamp_observations(service_rows.get(representative, []), metric)
                for row in selected_rows:
                    row.update({
                        "host_group_id": group["host_group_id"],
                        "physical_host_id": group["host_group_id"],
                        "host_shared": True,
                        "host_members": group_members,
                        "measurement_scope": "shared_host",
                        "host_metric_source_member": representative,
                        "physical_identity_confidence": group.get("physical_identity_confidence"),
                    })
                group["metric_sources"][metric] = representative
                group["metric_sample_count"] = len(selected_rows)
                for service in group_members:
                    service_rows[service] = selected_rows if service == representative else []
                    service_summary = summarize_member_observations({service: selected_rows}, metric).get(service, {})
                    add_metric_coverage({service: service_summary}, {service: selected_rows}, start, end, step_seconds)
                    service_summary.update({
                        "host_group_id": group["host_group_id"],
                        "host_shared": True,
                        "host_members": group_members,
                        "measurement_scope": "shared_host",
                        "host_metric_source_member": representative,
                        "physical_identity_confidence": group.get("physical_identity_confidence"),
                    })
                    metric_summaries[service] = service_summary
                    by_member.setdefault(service, {}).setdefault("representative_service_by_metric", {})[metric] = representative
            else:
                service = group_members[0]
                selected_rows = _select_timestamp_observations(service_rows.get(service, []), metric)
                identity = by_member.get(service, {})
                for row in selected_rows:
                    row.update({
                        "host_group_id": group.get("host_group_id"),
                        "physical_host_id": group.get("host_group_id"),
                        "host_shared": False,
                        "host_members": [service],
                        "measurement_scope": "PMM-inventoried node; physical-host uniqueness unverified",
                        "physical_identity_confidence": identity.get("physical_identity_confidence", "physical_host_unverified"),
                    })
                service_rows[service] = selected_rows
                service_summary = summarize_member_observations({service: selected_rows}, metric).get(service, {})
                add_metric_coverage({service: service_summary}, {service: selected_rows}, start, end, step_seconds)
                service_summary.update({
                    "host_group_id": group.get("host_group_id"),
                    "host_shared": False,
                    "host_members": [service],
                    "measurement_scope": "PMM-inventoried node; physical-host uniqueness unverified",
                    "physical_identity_confidence": identity.get("physical_identity_confidence", "physical_host_unverified"),
                })
                metric_summaries[service] = service_summary


def extract_member_observations(
    result: dict[str, Any], members: list[dict[str, Any]], metric_name: str,
) -> dict[str, list[dict[str, Any]]]:
    """Preserve timestamped samples and source labels, mapped to inventory members."""
    by_service = {str(member.get("service_name", "")): member for member in members}
    node_id_to_services: dict[str, list[str]] = {}
    node_name_to_services: dict[str, list[str]] = {}
    for inventory_member in members:
        if inventory_member.get("node_id") and inventory_member.get("service_name"):
            node_id_to_services.setdefault(str(inventory_member["node_id"]), []).append(
                str(inventory_member["service_name"])
            )
        node_name = inventory_member.get("node_name") or inventory_member.get("address")
        if node_name and inventory_member.get("service_name"):
            node_name_to_services.setdefault(str(node_name), []).append(
                str(inventory_member["service_name"])
            )
    out: dict[str, list[dict[str, Any]]] = {name: [] for name in by_service if name}
    data = result.get("data", {})
    series = data.get("result", []) if isinstance(data, dict) else []
    if not isinstance(series, list):
        return out
    for item in series:
        if not isinstance(item, dict) or not isinstance(item.get("metric"), dict):
            continue
        labels = dict(item["metric"])
        service = str(labels.get("service_name") or "")
        mapped_by_host_labels = not (service in by_service)
        if service in by_service:
            services = [service]
        else:
            services = node_id_to_services.get(str(labels.get("node_id", "")), [])
            if not services:
                services = node_name_to_services.get(str(labels.get("node_name", "")), [])
        if not services:
            continue
        for service in services:
            member = by_service.get(service)
            if not member:
                continue
            host_id = str(member.get("node_id") or member.get("node_name") or member.get("address") or "")
            host_members = sorted(set(services)) if mapped_by_host_labels else [service]
            if not host_members:
                host_members = [service]
            for point in item.get("values", []):
                if not isinstance(point, (list, tuple)) or len(point) < 2:
                    continue
                epoch, value = _number(point[0]), _number(point[1])
                if epoch is None or value is None:
                    continue
                timestamp = datetime.fromtimestamp(epoch, UTC).isoformat().replace("+00:00", "Z")
                out[service].append({
                    "timestamp": timestamp, "timestamp_epoch": epoch, "metric": metric_name,
                    "value": value, "unit": _METRIC_UNITS.get(metric_name, "unknown"),
                    "service_name": service, "node_id": member.get("node_id"),
                    "node_name": member.get("node_name") or member.get("address"),
                    "host_id": host_id or None, "host_shared": len(host_members) > 1,
                    "host_members": sorted(host_members), "labels": labels,
                    "source_node_id": labels.get("node_id"),
                    "source_matches_inventory_node_id": bool(
                        labels.get("node_id") and str(labels.get("node_id")) == str(member.get("node_id") or "")
                    ),
                    "source_instance": labels.get("instance"),
                    "source_job": labels.get("job"),
                })
    for observations in out.values():
        observations.sort(key=lambda observation: observation["timestamp_epoch"])
    return out


def detect_member_workload_peaks(
    result: dict[str, Any],
    members: list[dict[str, Any]],
    minimum_relative_peak: float = 1.5,
    minimum_samples: int = 3,
    limit: int = 3,
    dedup_window_seconds: int = 600,
) -> list[dict[str, object]]:
    """Return each member's sampled maximum and up to two distinct high events."""

    service_names = {str(member.get("service_name", "")) for member in members}
    node_id_to_service = {
        str(member.get("node_id")): str(member.get("service_name"))
        for member in members
        if member.get("node_id") and member.get("service_name")
    }
    node_name_to_service = {
        str(member.get("node_name") or member.get("address")): str(member.get("service_name"))
        for member in members
        if (member.get("node_name") or member.get("address")) and member.get("service_name")
    }
    data = result.get("data", {})
    series = data.get("result", []) if isinstance(data, dict) else []
    collected: dict[str, list[dict[str, Any]]] = {}
    if not isinstance(series, list):
        return []
    for item in series:
        if not isinstance(item, dict):
            continue
        labels = item.get("metric", {})
        if not isinstance(labels, dict):
            continue
        name = str(labels.get("service_name") or "")
        if name not in service_names:
            name = node_id_to_service.get(str(labels.get("node_id", "")), "")
        if not name:
            name = node_name_to_service.get(str(labels.get("node_name", "")), "")
        if name not in service_names:
            continue
        for point in item.get("values", []):
            if not isinstance(point, (list, tuple)) or len(point) < 2:
                continue
            timestamp, value = _number(point[0]), _number(point[1])
            if timestamp is not None and value is not None:
                collected.setdefault(name, []).append({
                    "timestamp_epoch": timestamp,
                    "value": value,
                    "labels": dict(labels),
                    "source_matches_inventory_node_id": bool(
                        labels.get("node_id")
                        and str(labels.get("node_id")) == str(
                            next((member.get("node_id") for member in members
                                  if str(member.get("service_name", "")) == name), "")
                        )
                    ),
                })

    peaks: list[dict[str, object]] = []
    for name, rows in collected.items():
        # Historical exporter re-registration can leave several label sets for
        # one PMM service. Use the same provenance-aware timestamp selection as
        # report summaries so overlapping old/new series cannot inflate a mean
        # or occupy multiple peak candidates.
        points = [
            (float(row["timestamp_epoch"]), float(row["value"]))
            for row in _select_timestamp_observations(rows, "operations_per_second")
        ]
        if len(points) < minimum_samples:
            continue
        points.sort()
        mean = sum(value for _, value in points) / len(points)
        # Greedy non-maximum suppression: nearby samples are one workload event.
        candidates = sorted(points, key=lambda point: (point[1], point[0]), reverse=True)
        selected: list[tuple[float, float]] = []
        for timestamp, value in candidates:
            if any(abs(timestamp - prior_ts) <= dedup_window_seconds for prior_ts, _ in selected):
                continue
            selected.append((timestamp, value))
            if len(selected) >= max(1, limit):
                break
        member_peaks = sorted(selected, key=lambda point: point[1], reverse=True)
        # Always report the sampled maximum, then retain up to two distinct
        # candidates. The relative threshold classifies significance without
        # suppressing the requested peak evidence.
        if member_peaks:
            maximum = max(points, key=lambda point: (point[1], point[0]))
            if maximum not in member_peaks:
                member_peaks.insert(0, maximum)
            member_peaks = member_peaks[:max(1, limit)]
        for rank, (timestamp, value) in enumerate(member_peaks):
            significant = rank == 0 or mean <= 0 or value >= mean * minimum_relative_peak
            peaks.append({"service_name": name, "timestamp": timestamp, "value": value,
                          "mean": mean, "samples": len(points), "sampled_peak": True,
                          "significant": significant})
    peaks.sort(key=lambda peak: (float(peak["value"]), float(peak["timestamp"])), reverse=True)
    return peaks


def refine_workload_peak_timestamps(
    peaks: list[dict[str, object]],
    detailed_observations: dict[str, list[dict[str, Any]]],
    detection_window_seconds: float,
) -> list[dict[str, object]]:
    """Replace coarse discovery timestamps with matching finer observations.

    A peak-discovery sample represents the maximum in the preceding coarse
    evaluation interval. Only detail rows in that interval can refine it; if
    no returned detail sample overlaps, retain the coarse evidence and mark it
    explicitly instead of manufacturing a timestamp or value.
    """

    refined: list[dict[str, object]] = []
    for peak in peaks:
        service = str(peak.get("service_name", ""))
        coarse_timestamp = _number(peak.get("timestamp"))
        if coarse_timestamp is None:
            refined.append(dict(peak))
            continue
        candidates = [
            row for row in _select_timestamp_observations(
                detailed_observations.get(service, []), "operations_per_second"
            )
            if coarse_timestamp - max(0.0, detection_window_seconds)
            <= float(row["timestamp_epoch"]) <= coarse_timestamp
        ]
        if not candidates:
            refined.append({
                **peak,
                "detection_timestamp": coarse_timestamp,
                "detection_value": peak.get("value"),
                "timestamp_refined": False,
                "timestamp_source": "coarse workload discovery sample; no aligned detail sample",
            })
            continue
        exact = max(candidates, key=lambda row: (float(row["value"]), float(row["timestamp_epoch"])))
        refined.append({
            **peak,
            "detection_timestamp": coarse_timestamp,
            "detection_value": peak.get("value"),
            "timestamp": float(exact["timestamp_epoch"]),
            "value": float(exact["value"]),
            "timestamp_refined": True,
            "timestamp_source": "maximum returned by targeted high-resolution query",
        })
    return refined


def merge_peak_detail_observations(
    report_rows: list[dict[str, Any]],
    detail_rows: list[dict[str, Any]],
    detail_start_epoch: float,
    detail_end_epoch: float,
    metric: str,
) -> list[dict[str, Any]]:
    """Replace coarse observations inside a bounded peak window with detail rows.

    The report-range series remains authoritative outside the targeted window.
    Within it, only observations returned by the finer query are retained; no
    interpolation or timestamp synthesis is performed.
    """
    outside = [
        row for row in report_rows
        if not detail_start_epoch <= float(row.get("timestamp_epoch", 0)) <= detail_end_epoch
    ]
    return _select_timestamp_observations(outside + list(detail_rows), metric)


def correlate_workload_peaks(
    peaks: list[dict[str, object]],
    observations: dict[str, dict[str, list[dict[str, Any]]]],
    members: list[dict[str, Any]],
    alignment_tolerance_seconds: float,
    event_window_seconds: int = 300,
    baseline_window_seconds: int = 900,
    host_identity_by_member: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Correlate observations near sampled workload timestamps without interpolation."""
    host_identity_by_member = host_identity_by_member or {}
    host_services: dict[str, set[str]] = {}
    for member in members:
        service = str(member.get("service_name", ""))
        identity = host_identity_by_member.get(service, {})
        host = str(identity.get("host_group_id") or member.get("node_id") or member.get("node_name") or member.get("address") or "")
        if host:
            host_services.setdefault(host, set()).add(service)

    def window_values(items: list[dict[str, Any]], start: float, end: float, anchors: list[float] | None) -> list[float]:
        return [float(item["value"]) for item in items
                if start <= float(item["timestamp_epoch"]) <= end
                and (anchors is None or (anchors and min(abs(float(item["timestamp_epoch"]) - a) for a in anchors) <= alignment_tolerance_seconds))]

    def window_items(items: list[dict[str, Any]], start: float, end: float, anchors: list[float]) -> list[dict[str, Any]]:
        return [item for item in items
                if start <= float(item["timestamp_epoch"]) <= end
                and anchors and min(abs(float(item["timestamp_epoch"]) - a) for a in anchors) <= alignment_tolerance_seconds]

    events = []
    for peak in peaks:
        service = str(peak["service_name"])
        ts = float(peak["timestamp"])
        # Report-range and targeted drill-down series can contain the same
        # timestamp. Deduplicate before constructing event/baseline anchors so
        # a finer observation supplements evidence without weighting it twice.
        op_items = _select_timestamp_observations(
            observations.get("operations_per_second", {}).get(service, []),
            "operations_per_second",
        )
        start, end = ts - event_window_seconds, ts + event_window_seconds
        baseline_start, baseline_end = ts - event_window_seconds - baseline_window_seconds, ts - event_window_seconds
        anchors = [float(item["timestamp_epoch"]) for item in op_items if start <= float(item["timestamp_epoch"]) <= end]
        baseline_anchors = [float(item["timestamp_epoch"]) for item in op_items
                            if baseline_start <= float(item["timestamp_epoch"]) < baseline_end]
        metric_rows: dict[str, Any] = {}
        for metric, services in observations.items():
            host_metric = metric in _HOST_METRIC_NAMES
            if host_metric:
                member = next((m for m in members if str(m.get("service_name")) == service), {})
                identity = host_identity_by_member.get(service, {})
                host = str(identity.get("host_group_id") or member.get("node_id") or member.get("node_name") or member.get("address") or "")
                host_services_for_member = host_services.get(host, set())
                source_service = str(identity.get("representative_service_by_metric", {}).get(metric) or "")
                if not source_service:
                    source_service = next((s for s, rows in services.items()
                                           if rows and str(rows[0].get("host_group_id") or rows[0].get("host_id") or "") == host), service)
                items = services.get(source_service, [])
                shared = bool(identity.get("shared_host") or len(host_services_for_member) > 1)
            else:
                identity = {}
                source_service = service
                items, shared = services.get(service, []), False
            matched_event_items = _select_timestamp_observations(window_items(items, start, end, anchors), metric)
            event = [float(item["value"]) for item in matched_event_items]
            matched_baseline_items = _select_timestamp_observations(
                window_items(items, baseline_start, baseline_end, baseline_anchors), metric
            )
            baseline = [float(item["value"]) for item in matched_baseline_items]
            missing_event_samples = max(0, len(anchors) - len({float(item["timestamp_epoch"]) for item in matched_event_items}))
            missing_baseline_samples = max(0, len(baseline_anchors) - len({float(item["timestamp_epoch"]) for item in matched_baseline_items}))
            metric_rows[metric] = {
                "status": "available" if event else "unavailable", "unit": _METRIC_UNITS.get(metric, "unknown"),
                "event_samples": len(event), "event_mean": sum(event) / len(event) if event else None,
                "event_min": min(event) if event else None, "event_max": max(event) if event else None,
                "expected_event_samples": len(anchors), "missing_event_samples": missing_event_samples,
                "event_coverage_percent": len({float(item["timestamp_epoch"]) for item in matched_event_items}) / len(anchors) * 100 if anchors else None,
                "baseline_samples": len(baseline), "baseline_mean": sum(baseline) / len(baseline) if len(baseline) >= 3 else None,
                "expected_baseline_samples": len(baseline_anchors), "missing_baseline_samples": missing_baseline_samples,
                "baseline_coverage_percent": len({float(item["timestamp_epoch"]) for item in matched_baseline_items}) / len(baseline_anchors) * 100 if baseline_anchors else None,
                "change_percent": ((sum(event) / len(event)) / (sum(baseline) / len(baseline)) - 1) * 100
                                  if event and len(baseline) >= 3 and sum(baseline) != 0 else None,
                "host_level": host_metric, "shared_host": shared,
                "host_group_id": identity.get("host_group_id") if host_metric else None,
                "host_members": identity.get("host_members", []) if host_metric else [],
                "physical_identity_confidence": identity.get("physical_identity_confidence") if host_metric else None,
                "measurement_scope": (
                    "shared_host" if shared else "PMM-inventoried node; physical-host uniqueness unverified"
                ) if host_metric else "member",
                "host_metric_source_member": source_service if host_metric else None,
                "event_observations": [{
                    "timestamp": item.get("timestamp"), "value": item.get("value"),
                    "unit": item.get("unit", _METRIC_UNITS.get(metric, "unknown")),
                    "labels": item.get("labels", {}),
                    "host_group_id": item.get("host_group_id"),
                    "source_node_id": item.get("source_node_id"),
                    "source_instance": item.get("source_instance"),
                    "source_job": item.get("source_job"),
                } for item in matched_event_items],
                "baseline_observations": [{
                    "timestamp": item.get("timestamp"), "value": item.get("value"),
                    "unit": item.get("unit", _METRIC_UNITS.get(metric, "unknown")),
                    "labels": item.get("labels", {}),
                } for item in matched_baseline_items],
                "timestamps": [item["timestamp"] for item in matched_event_items],
            }
        operations = window_values(op_items, start, end, anchors)
        baseline_operations = window_values(op_items, baseline_start, baseline_end, baseline_anchors)
        baseline_ops_mean = sum(baseline_operations) / len(baseline_operations) if len(baseline_operations) >= 3 else None
        event_ops_mean = sum(operations) / len(operations) if operations else None
        if event_ops_mean is not None and baseline_ops_mean not in (None, 0):
            ops_change_percent = (event_ops_mean / baseline_ops_mean - 1) * 100
        else:
            ops_change_percent = None
        events.append({
            "service_name": service, "peak_timestamp": datetime.fromtimestamp(ts, UTC).isoformat().replace("+00:00", "Z"),
            "peak_timestamp_epoch": ts,
            "peak_ops_per_second": peak["value"], "event_window_seconds": event_window_seconds,
            "significant": bool(peak.get("significant", False)),
            "baseline_window_seconds": baseline_window_seconds, "alignment_tolerance_seconds": alignment_tolerance_seconds,
            "operations_event_mean": sum(operations) / len(operations) if operations else None,
            "operations_event_samples": len(operations), "correlated_metrics": metric_rows,
            "operations_baseline_mean": baseline_ops_mean,
            "operations_baseline_samples": len(baseline_operations),
            "operations_change_percent": ops_change_percent,
            "baseline_window": {
                "start": datetime.fromtimestamp(baseline_start, UTC).isoformat().replace("+00:00", "Z"),
                "end": datetime.fromtimestamp(baseline_end, UTC).isoformat().replace("+00:00", "Z"),
            },
            "missing_evidence": sorted(metric for metric, row in metric_rows.items()
                                        if row["status"] != "available" or row["missing_event_samples"] > 0),
            "assessment": "Time-aligned co-movement is descriptive and does not establish causation.",
        })
    return events


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


def historical_role_at_timestamp(
    observations: list[dict[str, Any]], timestamp_epoch: float, tolerance_seconds: float,
) -> dict[str, Any]:
    """Resolve a member role only from samples close to the requested event time."""

    matched = [row for row in observations
               if abs(float(row.get("timestamp_epoch", 0)) - timestamp_epoch) <= tolerance_seconds]
    codes = sorted({int(float(row["value"])) for row in matched
                    if _number(row.get("value")) is not None and float(row["value"]).is_integer()})
    state = "primary" if codes == [1] else "secondary" if codes == [2] else "unknown"
    if len(codes) > 1:
        state = "conflicting"
    return {
        "status": state,
        "state_codes": codes,
        "samples": len(matched),
        "timestamps": [row.get("timestamp") for row in matched],
        "tolerance_seconds": tolerance_seconds,
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
    report = {
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
            "resources_and_capacity": {
                "percona_assessment": "Host metrics are mapped to PMM nodes; shared container-host views are grouped and labeled. Disk rates and latency represent the highest observed individual device, avoiding stacked-device double counting.",
                "metrics": {key: value for key, value in metrics.items() if key in {
                    "cpu_used_percent", "memory_used_percent", "root_filesystem_used_percent",
                    "disk_iops", "disk_io_latency_seconds", "disk_read_iops", "disk_write_iops",
                    "disk_read_latency_seconds", "disk_write_latency_seconds",
                }},
            },
            "mongodb_storage_engine_health": {
                "percona_assessment": "Evaluate dirty-page and cache occupancy together with eviction activity, queues, tickets, and latency; cache occupancy alone is not pressure.",
                "metrics": {key: value for key, value in metrics.items() if key in {
                    "wiredtiger_dirty_page_percent", "wiredtiger_cache_used_percent",
                    "wiredtiger_evictions_per_second",
                }},
            },
            "workload_and_concurrency": {
                "percona_assessment": "Interpret connection and operation trends alongside workload shape and latency; absent telemetry is not evidence of headroom.",
                "metrics": {key: value for key, value in metrics.items() if key in {
                    "connection_used_percent", "operations_per_second", "operation_latency_seconds",
                }},
            },
            "replication_and_cluster_health": {"percona_assessment": "Review replica lag by member and distinguish intentionally delayed members before classifying lag as instability.", "metrics": {key: value for key, value in metrics.items() if key == "replication_lag_seconds"}},
            "operational_events_and_alerts": {"status": "not_available", "percona_assessment": "PMM alert-event history is not collected by this MCP report. Correlate recurring alerts separately with the metrics above."},
            "workload_peak_analysis_and_metric_correlation": {
                "title": "Workload Peak Analysis and Metric Correlation",
                "events": [],
                "assessment": "Timestamp-aligned co-movement is observational and does not establish causation.",
            },
        },
        "limitations": [
            "Range summaries use available PMM samples only; gaps are not interpolated.",
            "Observational thresholds are report parameters, not the standard health-check policy.",
            "Replica-set member states are point-in-time PMM observations and do not provide a history of elections or role changes.",
        ],
    }
    report["report_validation"] = validate_performance_report(report)
    return report
