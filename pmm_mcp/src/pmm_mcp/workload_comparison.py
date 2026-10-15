"""Pure helpers for comparing Prometheus workload windows."""

from __future__ import annotations

import math
from typing import Any


def _percentile(values: list[float], percentile: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def summarize_range_result(result: dict[str, Any]) -> dict[str, Any]:
    """Reduce a Prometheus range response to comparable numeric statistics."""

    data = result.get("data", {})
    series = data.get("result", []) if isinstance(data, dict) else []
    values: list[float] = []
    series_count = 0
    if isinstance(series, list):
        for item in series:
            if not isinstance(item, dict) or not isinstance(item.get("values"), list):
                continue
            numeric: list[float] = []
            for point in item["values"]:
                if not isinstance(point, (list, tuple)) or len(point) < 2:
                    continue
                try:
                    value = float(point[1])
                except (TypeError, ValueError):
                    continue
                if math.isfinite(value):
                    numeric.append(value)
            if numeric:
                series_count += 1
                values.extend(numeric)
    if not values:
        return {
            "status": "unavailable", "samples": 0, "series": 0,
            "average": None, "p95": None, "minimum": None, "maximum": None,
        }
    return {
        "status": "ok", "samples": len(values), "series": series_count,
        "average": sum(values) / len(values), "p95": _percentile(values, 0.95),
        "minimum": min(values), "maximum": max(values),
    }


def compare_summaries(
    baseline: dict[str, Any], comparison: dict[str, Any], change_threshold_percent: float
) -> dict[str, Any]:
    """Compare two summary objects and classify the observed change."""

    baseline_value = baseline.get("average")
    comparison_value = comparison.get("average")
    if baseline.get("status") != "ok" or comparison.get("status") != "ok":
        return {
            "status": "unavailable", "changed": False, "delta": None,
            "delta_percent": None, "classification": "insufficient_data",
        }
    delta = comparison_value - baseline_value
    if baseline_value == 0:
        delta_percent = None
        changed = comparison_value != 0
    else:
        delta_percent = (delta / abs(baseline_value)) * 100
        changed = abs(delta_percent) >= change_threshold_percent
    return {
        "status": "ok", "changed": changed, "delta": delta,
        "delta_percent": delta_percent,
        "classification": "stable" if not changed else "increased" if delta > 0 else "decreased",
    }
