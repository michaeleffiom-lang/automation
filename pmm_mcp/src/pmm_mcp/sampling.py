"""Sampling and UTC-window helpers for PMM performance reports."""

from __future__ import annotations

import math
import re
from datetime import UTC, datetime, timedelta


_DURATION_PART = re.compile(r"(\d+(?:\.\d+)?)(ms|s|m|h|d|w|y)")
_UNIT_SECONDS = {
    "ms": 0.001,
    "s": 1,
    "m": 60,
    "h": 3600,
    "d": 86400,
    "w": 604800,
    "y": 31536000,
}
_MAX_RANGE_POINTS = 5000


def parse_prometheus_duration(value: str) -> float:
    """Return a Prometheus duration in seconds; reject partial or invalid input."""

    text = value.strip()
    if not text:
        raise ValueError("step deve ser uma duração Prometheus, como 30s, 5m ou 1h.")
    if text.isdigit():
        numeric_seconds = int(text)
        if numeric_seconds > 0:
            return float(numeric_seconds)
    position = 0
    seconds = 0.0
    for match in _DURATION_PART.finditer(text):
        if match.start() != position:
            raise ValueError("step deve ser uma duração Prometheus válida.")
        amount, unit = match.groups()
        seconds += float(amount) * _UNIT_SECONDS[unit]
        position = match.end()
    if position != len(text) or seconds <= 0:
        raise ValueError("step deve ser uma duração Prometheus válida.")
    return seconds


def format_prometheus_duration(seconds: float) -> str:
    """Format whole seconds as a compact Prometheus duration."""

    whole_seconds = max(1, math.ceil(seconds))
    for unit, divisor in (("y", 31536000), ("w", 604800), ("d", 86400),
                          ("h", 3600), ("m", 60)):
        if whole_seconds % divisor == 0:
            return f"{whole_seconds // divisor}{unit}"
    return f"{whole_seconds}s"


def select_report_step(
    period_hours: int,
    scrape_interval: str | None = None,
    requested_step: str | None = None,
    max_points: int = _MAX_RANGE_POINTS,
) -> str:
    """Choose a report step bounded by requested resolution, scrape cadence and query size."""

    if not 1 <= period_hours <= 24 * 90:
        raise ValueError("period_hours deve estar entre 1 e 2160 (90 dias).")
    duration_seconds = period_hours * 3600
    if requested_step:
        target_seconds = parse_prometheus_duration(requested_step)
    elif period_hours <= 24:
        target_seconds = 60
    elif period_hours <= 24 * 7:
        target_seconds = 5 * 60
    elif period_hours <= 24 * 30:
        target_seconds = 15 * 60
    else:
        target_seconds = 60 * 60

    scrape_seconds = parse_prometheus_duration(scrape_interval) if scrape_interval else 0
    point_limit_seconds = duration_seconds / max(1, max_points - 1)
    step_seconds = max(target_seconds, scrape_seconds, point_limit_seconds)
    return format_prometheus_duration(step_seconds)


def select_peak_drilldown_step(period_hours: int, scrape_interval: str | None = None) -> str:
    """Choose the finest practical read-only step for a short workload peak window."""

    target = "30s" if period_hours <= 24 else "1m"
    scrape_seconds = parse_prometheus_duration(scrape_interval) if scrape_interval else 0
    detail_window_seconds = 15 * 60
    point_limit_seconds = detail_window_seconds / max(1, _MAX_RANGE_POINTS - 1)
    return format_prometheus_duration(max(
        parse_prometheus_duration(target), scrape_seconds, point_limit_seconds
    ))


def resolve_utc_window(period_hours: int, end: str | None = None) -> tuple[str, str]:
    """Return the exact report window as UTC RFC3339 strings ending at ``end`` or now."""

    if not 1 <= period_hours <= 24 * 90:
        raise ValueError("period_hours deve estar entre 1 e 2160 (90 dias).")
    if end and end.strip():
        try:
            end_at = datetime.fromisoformat(end.strip().replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("end deve usar ISO-8601, por exemplo 2026-10-06T20:30:25Z.") from exc
        if end_at.tzinfo is None:
            raise ValueError("end deve incluir timezone, por exemplo o sufixo Z para UTC.")
        end_at = end_at.astimezone(UTC)
    else:
        end_at = datetime.now(UTC)
    start_at = end_at - timedelta(hours=period_hours)
    return (
        start_at.isoformat().replace("+00:00", "Z"),
        end_at.isoformat().replace("+00:00", "Z"),
    )


def bound_start_to_retention(
    requested_start: str, period_end: str, retention: str | None
) -> tuple[str, bool]:
    """Clip a query start to known retention without changing the requested window."""

    start_at = datetime.fromisoformat(requested_start.replace("Z", "+00:00")).astimezone(UTC)
    if not retention:
        return start_at.isoformat().replace("+00:00", "Z"), False
    end_at = datetime.fromisoformat(period_end.replace("Z", "+00:00")).astimezone(UTC)
    earliest_retained = end_at - timedelta(seconds=parse_prometheus_duration(retention))
    if earliest_retained > start_at:
        return earliest_retained.isoformat().replace("+00:00", "Z"), True
    return start_at.isoformat().replace("+00:00", "Z"), False
