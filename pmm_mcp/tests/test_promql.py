import unittest

from pmm_mcp.pmm_api import validate_promql
from pmm_mcp.performance_report import (
    build_replica_set_report,
    check_report_consistency,
    service_groups,
    summarize_member_series,
    summarize_member_role_series,
)
from pmm_mcp.workload_comparison import compare_summaries, summarize_range_result
from pmm_mcp.server import _report_metric_query, _report_role_query, _report_selector
from pmm_mcp.report_validation import validate_performance_report


class PromQLTests(unittest.TestCase):
    def test_accepts_read_only_promql(self) -> None:
        validate_promql('mongodb_up{service_name="mongodb8-mcp"}')

    def test_rejects_empty_or_multiple_statement_input(self) -> None:
        with self.assertRaises(ValueError):
            validate_promql("")
        with self.assertRaises(ValueError):
            validate_promql("up; drop")

    def test_summarizes_and_compares_range_values(self) -> None:
        result = {"data": {"result": [
            {"values": [[1, "10"], [2, "20"], [3, "30"]]},
            {"values": [[1, "20"], [2, "40"]]},
        ]}}
        baseline = summarize_range_result(result)
        comparison = summarize_range_result({"data": {"result": [{"values": [[1, "40"]]}]}})
        change = compare_summaries(baseline, comparison, 10)
        self.assertEqual(baseline["samples"], 5)
        self.assertEqual(baseline["series"], 2)
        self.assertTrue(change["changed"])
        self.assertEqual(change["classification"], "increased")

    def test_performance_report_keeps_missing_member_telemetry_visible(self) -> None:
        members = [
            {"service_name": "primary", "cluster": "orders", "replication_set": "rs0"},
            {"service_name": "secondary", "cluster": "orders", "replication_set": "rs0"},
        ]
        grouped = service_groups(members)
        self.assertEqual(grouped[0]["cluster"], "orders")
        series = summarize_member_series(
            {"data": {"result": [{"metric": {"service_name": "primary"}, "values": [[1, "81"], [2, "82"]]}]}},
            members,
        )
        self.assertEqual(series["primary"]["maximum"], 82.0)
        self.assertEqual(series["secondary"]["status"], "unavailable")
        report = build_replica_set_report(
            "orders", "rs0", members,
            {
                "cpu_used_percent": series,
                "root_filesystem_used_percent": {},
                "connection_used_percent": {},
                "wiredtiger_dirty_page_percent": {},
                "replication_lag_seconds": {},
            },
            {
                "cpu_used_percent": 80.0,
                "root_filesystem_used_percent": 80.0,
                "connection_used_percent": 60.0,
                "wiredtiger_dirty_page_percent": 20.0,
            },
        )
        self.assertEqual(report["title"], "Percona Managed Services Assessment")
        self.assertEqual(report["report_validation"]["status"], "warning")
        self.assertTrue(report["consistency_check"]["warnings"])
        self.assertEqual(len(report["executive_summary"]["findings"]), 1)
        self.assertIn("root_filesystem_used_percent", report["executive_summary"]["unavailable_metric_families"])

    def test_report_selector_uses_prometheus_re2_compatible_grouping(self) -> None:
        selector = _report_selector(["mflix-rs", "mongodb7-mcp"])
        self.assertIn("^(mflix-rs|mongodb7-mcp)$", selector)
        self.assertNotIn("?:", selector)

    def test_host_metric_queries_join_node_exporter_by_inventory_node_id(self) -> None:
        members = [
            {"service_name": "mflix-rs", "node_id": "node-1", "address": "mongodb7-rs1"},
            {"service_name": "mflix-rs2", "node_id": "node-2", "address": "mongodb7-rs2"},
        ]
        cpu_source, cpu_query = _report_metric_query(
            {"node_cpu_seconds_total"}, "cpu_used_percent", members
        )
        fallback_cpu_source, fallback_cpu_query = _report_metric_query(
            {"node_cpu_seconds_total"}, "cpu_used_percent", members,
            host_label_override="node_name",
        )
        filesystem_source, filesystem_query = _report_metric_query(
            {"node_filesystem_avail_bytes", "node_filesystem_size_bytes"},
            "root_filesystem_used_percent",
            members,
        )
        self.assertEqual(cpu_source, "node_cpu_seconds_total")
        self.assertIn('node_id=~"^(node-1|node-2)$"', cpu_query)
        self.assertIn("avg by (node_id,node_name,instance,job,agent_id,node_type)", cpu_query)
        self.assertNotIn("service_name", cpu_query)
        validate_promql(cpu_query)
        self.assertEqual(fallback_cpu_source, "node_cpu_seconds_total")
        self.assertIn('node_name=~"^(mongodb7-rs1|mongodb7-rs2)$"', fallback_cpu_query)
        self.assertIn("avg by (node_id,node_name,instance,job,agent_id,node_type)", fallback_cpu_query)
        self.assertEqual(cpu_query.split("rate(", 1)[1].split("[", 1)[1],
                         fallback_cpu_query.split("rate(", 1)[1].split("[", 1)[1])
        validate_promql(fallback_cpu_query)
        self.assertIn("mountpoint=\"/\"", filesystem_query)
        self.assertIn("max by (node_id,node_name,instance,job,agent_id,node_type,mountpoint,device,fstype)", filesystem_query)
        self.assertIn("node_id=~\"^(node-1|node-2)$\"", filesystem_query)
        self.assertIn("node_filesystem_avail_bytes / node_filesystem_size_bytes", filesystem_source)
        validate_promql(filesystem_query)

    def test_disk_metrics_keep_device_provenance_and_avoid_stacked_device_sums(self) -> None:
        members = [{"service_name": "mongo-a", "node_id": "node-1", "address": "host-a"}]
        available = {
            "node_disk_reads_completed_total", "node_disk_writes_completed_total",
            "node_disk_read_time_seconds_total", "node_disk_write_time_seconds_total",
        }
        for metric in ("disk_iops", "disk_read_iops", "disk_write_iops",
                       "disk_io_latency_seconds", "disk_read_latency_seconds",
                       "disk_write_latency_seconds"):
            source, query = _report_metric_query(available, metric, members)
            self.assertIsNotNone(source)
            self.assertIn("device)", query)
            self.assertIn("node_id", query)
            if metric.endswith("iops") or metric == "disk_iops":
                self.assertIn("rate(", query)
                self.assertNotIn("sum by (node_id,node_name,instance,job,agent_id,node_type)", query)
            validate_promql(query)

        _, operation_latency = _report_metric_query(
            {"mongodb_mongod_op_latencies_latency_total", "mongodb_mongod_op_latencies_ops_total"},
            "operation_latency_seconds", members,
        )
        self.assertIn("rate(mongodb_mongod_op_latencies_latency_total", operation_latency)
        self.assertIn("rate(mongodb_mongod_op_latencies_ops_total", operation_latency)
        self.assertIn("/ 1000000", operation_latency)
        validate_promql(operation_latency)

    def test_missing_source_metric_does_not_create_a_fabricated_query(self) -> None:
        source, query = _report_metric_query(
            set(), "disk_read_iops", [{"service_name": "mongo-a", "node_id": "node-1"}]
        )
        self.assertEqual(source, "node_disk_reads_completed_total")
        self.assertIsNone(query)

    def test_report_sections_expose_restored_resource_storage_and_latency_metrics(self) -> None:
        metrics = {
            name: {"mongo-a": {"status": "ok", "samples": 3}}
            for name in (
                "cpu_used_percent", "memory_used_percent", "root_filesystem_used_percent",
                "disk_read_iops", "disk_write_iops", "disk_read_latency_seconds",
                "disk_write_latency_seconds", "wiredtiger_cache_used_percent",
                "wiredtiger_evictions_per_second", "operation_latency_seconds",
            )
        }
        report = build_replica_set_report(
            "orders", "rs0", [{"service_name": "mongo-a"}], metrics,
            {"cpu_used_percent": 80, "root_filesystem_used_percent": 80,
                "connection_used_percent": 60, "wiredtiger_dirty_page_percent": 20},
        )
        resources = report["sections"]["resources_and_capacity"]["metrics"]
        storage = report["sections"]["mongodb_storage_engine_health"]["metrics"]
        workload = report["sections"]["workload_and_concurrency"]["metrics"]
        self.assertIn("memory_used_percent", resources)
        self.assertIn("disk_read_iops", resources)
        self.assertIn("disk_write_latency_seconds", resources)
        self.assertIn("wiredtiger_cache_used_percent", storage)
        self.assertIn("wiredtiger_evictions_per_second", storage)
        self.assertIn("operation_latency_seconds", workload)

    def test_wiredtiger_dirty_percentage_uses_available_server_status_metrics(self) -> None:
        members = [{"service_name": "mflix-rs", "node_id": "node-1"}]
        source, query = _report_metric_query(
            {
                "mongodb_ss_wt_cache_tracked_dirty_bytes_in_the_cache",
                "mongodb_mongod_wiredtiger_cache_max_bytes",
            },
            "wiredtiger_dirty_page_percent",
            members,
        )
        self.assertEqual(
            source,
            "mongodb_ss_wt_cache_tracked_dirty_bytes_in_the_cache / "
            "mongodb_mongod_wiredtiger_cache_max_bytes",
        )
        self.assertIn("100 * max by (service_name,node_id,node_name,instance,job,agent_id,service_id)", query)
        self.assertIn("mongodb_ss_wt_cache_tracked_dirty_bytes_in_the_cache", query)
        self.assertIn("mongodb_mongod_wiredtiger_cache_max_bytes", query)
        validate_promql(query)

    def test_member_summarizer_maps_node_exporter_series_to_service_by_node_id(self) -> None:
        members = [
            {"service_name": "mflix-rs", "node_id": "node-1"},
            {"service_name": "mflix-rs2", "node_id": "node-2"},
        ]
        summary = summarize_member_series(
            {"data": {"result": [
                {"metric": {"node_id": "node-1", "node_name": "mongodb7-rs1"}, "values": [[1, "20"], [2, "30"]]},
                {"metric": {"node_id": "node-2", "node_name": "mongodb7-rs2"}, "values": [[1, "40"]]},
            ]}},
            members,
        )
        self.assertEqual(summary["mflix-rs"]["maximum"], 30.0)
        self.assertEqual(summary["mflix-rs"]["samples"], 2)
        self.assertEqual(summary["mflix-rs2"]["latest"], 40.0)

    def test_performance_report_queries_member_role_metric_when_available(self) -> None:
        source, query = _report_role_query(
            {"mongodb_mongod_replset_my_state"}, ["mflix-rs", "mflix-rs2"]
        )
        self.assertEqual(source, "mongodb_mongod_replset_my_state")
        self.assertEqual(
            query,
            'mongodb_mongod_replset_my_state{service_name=~"^(mflix-rs|mflix-rs2)$"}',
        )
        self.assertEqual(_report_role_query(set(), ["mflix-rs"]), (None, None))

    def test_role_metric_populates_topology_and_passes_consistency_check(self) -> None:
        members = [
            {"service_name": "mflix-rs"},
            {"service_name": "mflix-rs2"},
            {"service_name": "mflix-rs3"},
        ]
        roles = summarize_member_role_series(
            {"data": {"result": [
                {"metric": {"service_name": "mflix-rs"}, "value": [100, "1"]},
                {"metric": {"service_name": "mflix-rs2"}, "value": [100, "2"]},
                {"metric": {"service_name": "mflix-rs3"}, "value": [100, "2"]},
            ]}},
            members,
        )
        report = build_replica_set_report(
            "orders", "rs0", members, {}, {
                "cpu_used_percent": 80.0,
                "root_filesystem_used_percent": 80.0,
                "connection_used_percent": 60.0,
                "wiredtiger_dirty_page_percent": 20.0,
            }, roles, role_metric_available=True
        )
        self.assertEqual(
            [member["role_state"] for member in report["topology"]["members"]],
            ["primary", "secondary", "secondary"],
        )
        self.assertEqual(report["consistency_check"]["status"], "passed")
        self.assertEqual(report["consistency_check"]["checks"]["single_primary"], "passed")
        self.assertEqual(report["report_validation"]["status"], "passed")

    def test_report_validator_rejects_missing_consistency_and_topology_members(self) -> None:
        result = validate_performance_report({
            "report_type": "mongodb_cluster_performance_review",
            "title": "Assessment",
            "scope": {"cluster": "orders", "replica_set": "rs0", "member_count": 1},
            "executive_summary": {},
            "topology": {"members": []},
            "sections": {},
            "limitations": [],
        })
        self.assertEqual(result["status"], "invalid")
        self.assertTrue(any("consistency_check" in error for error in result["errors"]))
        self.assertTrue(any("member_count" in error for error in result["errors"]))

    def test_role_consistency_check_flags_missing_and_multiple_primary_states(self) -> None:
        members = [{"service_name": "one"}, {"service_name": "two"}]
        multiple_primaries = summarize_member_role_series(
            {"data": {"result": [
                {"metric": {"service_name": "one"}, "value": [100, "1"]},
                {"metric": {"service_name": "two"}, "value": [100, "1"]},
            ]}},
            members,
        )
        result = check_report_consistency(members, multiple_primaries, True)
        self.assertEqual(result["status"], "warning")
        self.assertEqual(result["checks"]["single_primary"], "warning")
        self.assertTrue(any("exactly one" in warning for warning in result["warnings"]))

        incomplete = summarize_member_role_series(
            {"data": {"result": [
                {"metric": {"service_name": "one"}, "value": [100, "1"]},
            ]}},
            members,
        )
        result = check_report_consistency(members, incomplete, True)
        self.assertEqual(result["checks"]["role_state_coverage"], "warning")
        self.assertTrue(any("missing or conflicting" in warning for warning in result["warnings"]))


if __name__ == "__main__":
    unittest.main()
