import unittest
from datetime import UTC, datetime
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch

from pmm_mcp.config import PMMClientConfig
from pmm_mcp.performance_report import detect_member_workload_peaks, summarize_member_series
from pmm_mcp.pmm_api import PMMClient
from pmm_mcp.sampling import (
    bound_start_to_retention,
    parse_prometheus_duration,
    resolve_utc_window,
    select_peak_drilldown_step,
    select_report_step,
)
from pmm_mcp import server


class ReportSamplingTests(unittest.TestCase):
    def test_selects_report_step_for_requested_duration(self) -> None:
        self.assertEqual(select_report_step(1), "1m")
        self.assertEqual(select_report_step(24), "1m")
        self.assertEqual(select_report_step(24 * 7), "5m")
        self.assertEqual(select_report_step(24 * 30), "15m")
        self.assertEqual(select_report_step(24 * 90), "1h")

    def test_report_step_respects_scrape_interval_explicit_step_and_point_limit(self) -> None:
        self.assertEqual(select_report_step(1, scrape_interval="2m"), "2m")
        self.assertEqual(
            select_report_step(24, scrape_interval="1m", requested_step="30s"),
            "1m",
        )
        long_step = select_report_step(24 * 90, max_points=5000)
        returned_points = (24 * 90 * 3600) // parse_prometheus_duration(long_step) + 1
        self.assertLessEqual(returned_points, 5000)

    def test_peak_drilldown_step_is_fine_but_not_finer_than_scrape_interval(self) -> None:
        self.assertEqual(select_peak_drilldown_step(1, "15s"), "30s")
        self.assertEqual(select_peak_drilldown_step(24 * 7, "2m"), "2m")
        self.assertEqual(select_peak_drilldown_step(1, "100ms"), "30s")

    def test_report_window_boundaries_are_normalized_to_utc(self) -> None:
        start, end = resolve_utc_window(1, "2026-10-18T14:00:00+03:00")
        self.assertEqual(start, "2026-10-18T10:00:00Z")
        self.assertEqual(end, "2026-10-18T11:00:00Z")

    def test_retention_clips_query_start_but_preserves_requested_window(self) -> None:
        start, clipped = bound_start_to_retention(
            "2026-10-01T00:00:00Z", "2026-10-18T00:00:00Z", "7d"
        )
        self.assertEqual(start, "2026-10-11T00:00:00Z")
        self.assertTrue(clipped)
        unchanged, clipped = bound_start_to_retention(
            "2026-10-01T00:00:00Z", "2026-10-18T00:00:00Z", "30d"
        )
        self.assertEqual(unchanged, "2026-10-01T00:00:00Z")
        self.assertFalse(clipped)

    def test_selected_step_is_passed_to_prometheus_query_range(self) -> None:
        config = PMMClientConfig(
            client_id="test",
            base_url="https://pmm.example",
            api_base_url="https://pmm.example/v1",
            service_discovery_url="https://pmm.example/prometheus/service-discovery",
            username=None,
            password=None,
            token=None,
            tls_insecure=False,
            timeout=1,
        )
        client = PMMClient(config)
        captured: list[str] = []

        def fake_json(url: str) -> dict[str, object]:
            captured.append(url)
            return {"data": {"resultType": "matrix", "result": []}}

        with patch.object(client, "_json", side_effect=fake_json):
            client.prometheus_query(
                "up", "2026-10-18T10:00:00Z", "2026-10-18T11:00:00Z",
                select_report_step(1), 5000,
            )
        self.assertIn("/prometheus/api/v1/query_range?", captured[0])
        self.assertEqual(parse_qs(urlsplit(captured[0]).query)["step"], ["1m"])

    def test_scrape_interval_uses_matching_active_targets_only(self) -> None:
        config = PMMClientConfig(
            client_id="test",
            base_url="https://pmm.example",
            api_base_url="https://pmm.example/v1",
            service_discovery_url="https://pmm.example/prometheus/service-discovery",
            username=None,
            password=None,
            token=None,
            tls_insecure=False,
            timeout=1,
        )
        client = PMMClient(config)
        payload = {"data": {"activeTargets": [
            {"labels": {"service_name": "primary"}, "scrapeInterval": "30s"},
            {"labels": {"node_id": "node-1"}, "scrapeInterval": "1m"},
            {"labels": {"service_name": "unrelated"}, "scrapeInterval": "5m"},
        ]}}
        with patch.object(client, "_json_value", return_value=payload):
            interval = client.prometheus_scrape_interval([
                {"service_name": "primary", "node_id": "node-1"}
            ])
        self.assertEqual(interval, "1m")

    def test_scrape_interval_reads_discovered_label_when_top_level_is_absent(self) -> None:
        config = PMMClientConfig(
            client_id="test",
            base_url="https://pmm.example",
            api_base_url="https://pmm.example/v1",
            service_discovery_url="https://pmm.example/prometheus/service-discovery",
            username=None,
            password=None,
            token=None,
            tls_insecure=False,
            timeout=1,
        )
        client = PMMClient(config)
        payload = {"data": {"activeTargets": [
            {
                "labels": {"node_id": "node-1", "job": "node_exporter_node-1_hr"},
                "discoveredLabels": {"__scrape_interval__": "5s"},
            },
            {
                "labels": {"node_id": "unrelated"},
                "discoveredLabels": {"__scrape_interval__": "1m"},
            },
        ]}}
        with patch.object(client, "_json_value", return_value=payload):
            interval = client.prometheus_scrape_interval([
                {"service_name": "primary", "node_id": "node-1"}
            ])
        self.assertEqual(interval, "5s")

    def test_retention_is_read_from_prometheus_status_flags_when_exposed(self) -> None:
        config = PMMClientConfig(
            client_id="test",
            base_url="https://pmm.example",
            api_base_url="https://pmm.example/v1",
            service_discovery_url="https://pmm.example/prometheus/service-discovery",
            username=None,
            password=None,
            token=None,
            tls_insecure=False,
            timeout=1,
        )
        client = PMMClient(config)
        with patch.object(client, "_json_value", return_value={
            "data": {"storage.tsdb.retention.time": "15d"}
        }):
            retention = client.prometheus_retention()
        self.assertEqual(retention, "15d")

    def test_sample_counts_reflect_returned_points_without_filling_gaps(self) -> None:
        members = [{"service_name": "primary"}, {"service_name": "secondary"}]
        points = [[index * 60, str(index)] for index in range(61)]
        result = {"data": {"result": [{"metric": {"service_name": "primary"}, "values": points}]}}
        summary = summarize_member_series(result, members)
        self.assertEqual(summary["primary"]["samples"], 61)
        self.assertEqual(summary["secondary"]["samples"], 0)
        self.assertEqual(summary["secondary"]["status"], "unavailable")

    def test_detects_sampled_workload_spike_for_drilldown(self) -> None:
        members = [{"service_name": "primary"}]
        result = {"data": {"result": [{
            "metric": {"service_name": "primary"},
            "values": [[0, "10"], [60, "11"], [120, "50"], [180, "12"]],
        }]}}
        peaks = detect_member_workload_peaks(result, members)
        self.assertEqual(len(peaks), 1)
        self.assertEqual(peaks[0]["timestamp"], 120.0)
        self.assertEqual(peaks[0]["value"], 50.0)

    def test_report_passes_utc_window_and_adaptive_steps_and_keeps_counts(self) -> None:
        class FakeClient:
            def __init__(self) -> None:
                self.calls: list[dict[str, object]] = []
                self.config = object()
                self.members = [
                    {"service_name": "primary", "node_id": "node-1", "cluster": "orders", "replication_set": "rs0"},
                    {"service_name": "secondary", "node_id": "node-2", "cluster": "orders", "replication_set": "rs0"},
                ]

            def mongodb_services(self) -> list[dict[str, object]]:
                return self.members

            def prometheus_scrape_interval(self, members: list[dict[str, object]]) -> str:
                return "15s"

            def prometheus_retention(self) -> str:
                return "30d"

            def prometheus_query(self, query: str, start: str | None = None,
                                 end: str | None = None, step: str = "1m",
                                 max_results: int = 1000) -> dict[str, object]:
                self.calls.append({"query": query, "start": start, "end": end, "step": step})
                if start is None:
                    return {"data": {"result": [
                        {"metric": {"service_name": "primary"}, "value": [1, "1"]},
                        {"metric": {"service_name": "secondary"}, "value": [1, "2"]},
                    ]}}
                start_epoch = datetime.fromisoformat(start.replace("Z", "+00:00")).timestamp()
                if "mongodb_op_counters_total" in query:
                    points = [[start_epoch + offset * 60, str(value)] for offset, value in enumerate((10, 50, 10))]
                    return {"data": {"result": [{
                        "metric": {"service_name": "primary"}, "values": points,
                    }]}}
                return {"data": {"result": [
                    {"metric": {"service_name": "primary", "node_id": "node-1"}, "values": [[start_epoch, "20"], [start_epoch + 60, "21"]]},
                    {"metric": {"service_name": "secondary", "node_id": "node-2"}, "values": [[start_epoch, "20"], [start_epoch + 60, "21"]]},
                ]}}

        client = FakeClient()
        catalog = {"metrics": {"prometheus": [
            "node_cpu_seconds_total",
            "node_filesystem_avail_bytes",
            "node_filesystem_size_bytes",
            "mongodb_connections",
            "mongodb_ss_wt_cache_tracked_dirty_bytes_in_the_cache",
            "mongodb_mongod_wiredtiger_cache_max_bytes",
            "mongodb_mongod_replset_member_replication_lag",
            "mongodb_op_counters_total",
            "mongodb_mongod_replset_my_state",
        ]}}
        with (
            patch.object(server, "_resolve_client", return_value=("test", client)),
            patch.object(server, "_client_catalog", return_value=(catalog, None)),
            patch.object(server, "_client_public_info", return_value={"client_id": "test"}),
        ):
            result = server.generate_performance_reports(
                client_id="test", period_hours=1,
                end="2026-10-18T14:00:00+03:00", cluster="orders",
            )

        report = result["reports"][0]
        self.assertEqual(report["period"]["start"], "2026-10-18T10:00:00Z")
        self.assertEqual(report["period"]["end"], "2026-10-18T11:00:00Z")
        self.assertEqual(report["period"]["step"], "1m")
        operation_summary = report["sections"]["workload_and_concurrency"]["metrics"]["operations_per_second"]
        self.assertEqual(operation_summary["primary"]["samples"], 3)
        drilldown = report["sections"]["workload_and_concurrency"]["peak_drilldown"]
        self.assertEqual(drilldown["status"], "available")
        self.assertEqual(drilldown["step"], "30s")
        range_calls = [call for call in client.calls if call["start"] is not None]
        self.assertTrue(all(call["step"] in {"1m", "30s"} for call in range_calls))
        self.assertTrue(any(call["step"] == "30s" for call in range_calls))


if __name__ == "__main__":
    unittest.main()
