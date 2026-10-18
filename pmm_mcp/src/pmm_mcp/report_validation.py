"""Consistency and structure checks for generated MongoDB reports."""

from __future__ import annotations

import math
from typing import Any


def _number(value: object) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def check_report_consistency(
    members: list[dict[str, Any]],
    role_summary: dict[str, dict[str, object]],
    role_metric_available: bool,
) -> dict[str, object]:
    """Check member role coverage and the single-primary replica-set invariant."""

    names = [str(member.get("service_name", "")) for member in members if member.get("service_name")]
    observed = {name: role_summary.get(name, {}) for name in names}
    complete = bool(names) and all(
        item.get("status") == "ok" and item.get("role_state") != "unknown"
        for item in observed.values()
    )
    warnings: list[str] = []
    if not role_metric_available:
        coverage_status = "unavailable"
        primary_status = "not_checked"
        warnings.append(
            "Replica-set member roles could not be checked because mongodb_mongod_replset_my_state is unavailable."
        )
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
        "checks": {"role_state_coverage": coverage_status, "single_primary": primary_status},
        "warnings": warnings,
    }


def validate_performance_report(report: dict[str, Any]) -> dict[str, object]:
    """Validate required report sections and consistency-check coverage.

    Missing telemetry remains a warning because it is a valid report limitation;
    malformed or incomplete report structure is invalid.
    """

    required = (
        "report_type", "title", "scope", "executive_summary", "topology",
        "consistency_check", "sections", "limitations",
    )
    missing = [key for key in required if key not in report]
    errors: list[str] = []
    warnings: list[str] = []
    if missing:
        errors.append("Required report fields are missing: " + ", ".join(missing) + ".")

    scope = report.get("scope")
    topology = report.get("topology")
    if isinstance(scope, dict) and isinstance(topology, dict):
        expected = scope.get("member_count")
        members = topology.get("members")
        if not isinstance(members, list):
            errors.append("topology.members must be a list.")
        elif expected != len(members):
            errors.append("scope.member_count does not match topology.members.")
        elif any(not isinstance(member, dict) or not member.get("service_name") for member in members):
            errors.append("Every topology member must include service_name.")
        elif any(not member.get("role_state") for member in members):
            errors.append("Every topology member must include role_state, using unknown when unavailable.")

    consistency = report.get("consistency_check")
    if not isinstance(consistency, dict):
        errors.append("consistency_check must be present as an object.")
    else:
        checks = consistency.get("checks")
        if not isinstance(checks, dict) or not {"role_state_coverage", "single_primary"}.issubset(checks):
            errors.append("consistency_check must include role coverage and single-primary checks.")
        if consistency.get("warnings"):
            warnings.extend(str(warning) for warning in consistency["warnings"])
        if consistency.get("status") in {"warning", "not_available"} and not consistency.get("warnings"):
            warnings.append("Consistency check has a warning status without an explanation.")

    status = "invalid" if errors else "warning" if warnings else "passed"
    return {"status": status, "errors": errors, "warnings": warnings}
