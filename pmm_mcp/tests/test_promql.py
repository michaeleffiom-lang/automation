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
from pmm_mcp.server import _report_role_query, _report_selector


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
        self.assertEqual(len(report["executive_summary"]["findings"]), 1)
        self.assertIn("root_filesystem_used_percent", report["executive_summary"]["unavailable_metric_families"])

    def test_report_selector_uses_prometheus_re2_compatible_grouping(self) -> None:
        selector = _report_selector(["mflix-rs", "mongodb7-mcp"])
        self.assertIn("^(mflix-rs|mongodb7-mcp)$", selector)
        self.assertNotIn("?:", selector)

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
