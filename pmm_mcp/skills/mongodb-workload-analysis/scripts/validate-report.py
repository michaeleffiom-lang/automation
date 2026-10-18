#!/usr/bin/env python3
"""Validate one report or a tool response containing reports from JSON input."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from pmm_mcp.report_validation import validate_performance_report  # noqa: E402


def _reports(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict) and isinstance(payload.get("reports"), list):
        return [item for item in payload["reports"] if isinstance(item, dict)]
    if isinstance(payload, dict) and isinstance(payload.get("report"), dict):
        return [payload["report"]]
    if isinstance(payload, dict):
        return [payload]
    return []


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("json_file", nargs="?", help="JSON file to validate; reads stdin when omitted")
    args = parser.parse_args()
    try:
        raw = Path(args.json_file).read_text(encoding="utf-8") if args.json_file else sys.stdin.read()
        payload = json.loads(raw)
    except (OSError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "invalid", "errors": [str(exc)]}, indent=2))
        return 2

    reports = _reports(payload)
    if not reports:
        print(json.dumps({"status": "invalid", "errors": ["No report object found in input."]}, indent=2))
        return 2
    results = [validate_performance_report(report) for report in reports]
    overall = "invalid" if any(result["status"] == "invalid" for result in results) else (
        "warning" if any(result["status"] == "warning" for result in results) else "passed"
    )
    print(json.dumps({"status": overall, "reports": results}, indent=2))
    return 1 if overall == "invalid" else 0


if __name__ == "__main__":
    raise SystemExit(main())
