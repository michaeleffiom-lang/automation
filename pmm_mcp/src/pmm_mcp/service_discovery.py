"""Fetch and parse PMM/VictoriaMetrics service-discovery HTML."""

from __future__ import annotations

import re
from html.parser import HTMLParser
from typing import Any


_LABEL_RE = re.compile(
    r"(?P<key>[A-Za-z_][A-Za-z0-9_]*)\s*=\s*\"(?P<value>(?:\\.|[^\"])*)\""
)
_HEADING_RE = re.compile(r"^(?P<job>.*?)\s+\((?P<activity>[^)]+)\)")


def parse_labels(value: str) -> dict[str, str]:
    """Parse a Prometheus label set such as ``{job=\"mongodb\"}``."""

    labels: dict[str, str] = {}
    for match in _LABEL_RE.finditer(value):
        label_value = match.group("value")
        label_value = label_value.replace(r'\"', '"').replace(r"\\", "\\")
        labels[match.group("key")] = label_value
    return labels


class _DiscoveryParser(HTMLParser):
    """Extract scrape-job rows from the HTML returned by PMM."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._in_heading = False
        self._heading_text: list[str] = []
        self._in_row = False
        self._cell_depth = 0
        self._cell_text: list[str] = []
        self._cells: list[str] = []
        self._label_cell = False
        self.current_job = ""
        self.current_activity = ""
        self.rows: list[dict[str, Any]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag == "h4":
            self._in_heading = True
            self._heading_text = []
        elif tag == "tr":
            self._in_row = True
            self._cells = []
        elif self._in_row and tag == "td" and self._cell_depth == 0:
            self._cell_depth = 1
            self._cell_text = []
            self._label_cell = "labels" in (attributes.get("class") or "").split()
        elif self._cell_depth:
            self._cell_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag == "h4" and self._in_heading:
            heading = " ".join("".join(self._heading_text).split())
            match = _HEADING_RE.match(heading)
            if match:
                self.current_job = match.group("job").strip()
                self.current_activity = match.group("activity").strip()
            self._in_heading = False
            self._heading_text = []
        elif tag == "td" and self._cell_depth == 1:
            text = "".join(self._cell_text).strip()
            self._cells.append(text)
            self._cell_depth = 0
            self._cell_text = []
            self._label_cell = False
        elif self._cell_depth:
            self._cell_depth -= 1
        elif tag == "tr" and self._in_row:
            self._in_row = False
            if len(self._cells) >= 3 and self.current_job:
                self.rows.append(
                    {
                        "job": self.current_job,
                        "activity": self.current_activity,
                        "status": self._cells[0].upper(),
                        "discovered_labels": parse_labels(self._cells[1]),
                        "target_labels": parse_labels(self._cells[2]),
                    }
                )
            self._cells = []

    def handle_data(self, data: str) -> None:
        if self._in_heading:
            self._heading_text.append(data)
        elif self._cell_depth:
            self._cell_text.append(data)


def parse_discovery_html(document: str) -> list[dict[str, Any]]:
    """Return all discovered targets represented in PMM's HTML document."""

    parser = _DiscoveryParser()
    parser.feed(document)
    parser.close()
    return parser.rows


def mongodb_services(document: str) -> list[dict[str, Any]]:
    """Return only targets that PMM identifies as MongoDB services."""

    services: list[dict[str, Any]] = []
    for row in parse_discovery_html(document):
        discovered = row["discovered_labels"]
        target = row["target_labels"]
        if not (
            discovered.get("service_type") == "mongodb"
            or target.get("service_type") == "mongodb"
            or "mongodb" in row["job"].lower()
        ):
            continue

        services.append(
            {
                "service_id": target.get("service_id") or discovered.get("service_id", ""),
                "service_name": target.get("service_name") or discovered.get("service_name", ""),
                "service_type": "mongodb",
                "status": row["status"],
                "job": row["job"],
                "activity": row["activity"],
                "address": discovered.get("__address__", ""),
                "metrics_path": discovered.get("__metrics_path__", ""),
                "node_id": target.get("node_id") or discovered.get("node_id", ""),
                "node_name": target.get("node_name") or discovered.get("node_name", ""),
                "agent_id": target.get("agent_id") or discovered.get("agent_id", ""),
                "agent_type": target.get("agent_type") or discovered.get("agent_type", ""),
                "discovered_labels": discovered,
                "target_labels": target,
            }
        )
    return services


def merge_mongodb_inventory(
    inventory: dict[str, Any],
    agents_inventory: dict[str, Any],
    discovery_targets: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Build stable MongoDB identities from PMM inventory and exporter data."""

    agents_by_service: dict[str, list[dict[str, Any]]] = {}
    for group in agents_inventory.values():
        for agent in group:
            service_id = str(agent.get("service_id", ""))
            if service_id:
                agents_by_service.setdefault(service_id, []).append(
                    {
                        "agent_id": agent.get("agent_id", ""),
                        "pmm_agent_id": agent.get("pmm_agent_id", ""),
                        "status": agent.get("status", ""),
                        "disabled": bool(agent.get("disabled", False)),
                        "listen_port": agent.get("listen_port"),
                        "metrics_mode": "push" if agent.get("push_metrics_enabled") else "pull",
                        "agent_type": (
                            "mongodb_exporter"
                            if "mongodb_exporter" in str(agent.get("process_exec_path", ""))
                            else "agent"
                        ),
                    }
                )

    targets_by_service: dict[str, list[dict[str, Any]]] = {}
    for target in discovery_targets:
        service_id = str(target.get("service_id", ""))
        if service_id:
            targets_by_service.setdefault(service_id, []).append(target)

    result: list[dict[str, Any]] = []
    for service in inventory.get("mongodb", []):
        service_id = str(service.get("service_id", ""))
        result.append(
            {
                "service_id": service_id,
                "service_name": service.get("service_name", ""),
                "service_type": "mongodb",
                "address": service.get("address", ""),
                "port": service.get("port"),
                "node_id": service.get("node_id", ""),
                "environment": service.get("environment", ""),
                "cluster": service.get("cluster", ""),
                "replication_set": service.get("replication_set", ""),
                "version": service.get("version", ""),
                "agents": agents_by_service.get(service_id, []),
                "service_discovery_targets": targets_by_service.get(service_id, []),
            }
        )
    return result
