from datetime import UTC, datetime
import unittest

from pmm_mcp.performance_report import (
    apply_host_topology,
    add_metric_coverage,
    attach_peak_detail_evidence,
    correlate_workload_peaks,
    detect_member_workload_peaks,
    expected_range_samples,
    extract_member_observations,
    historical_role_at_timestamp,
    infer_host_topology,
    merge_peak_detail_observations,
    needs_host_label_fallback,
    prefer_more_complete_observations,
    refine_workload_peak_timestamps,
    summarize_member_observations,
)
from pmm_mcp.sampling import parse_prometheus_duration, select_report_step


def epoch(minute: int) -> float:
    return datetime(2026, 10, 18, 1, 0, tzinfo=UTC).timestamp() + minute * 60


def range_result(points, labels=None):
    return {"data": {"result": [{"metric": labels or {}, "values": points}]}}


class TimeCorrelationTests(unittest.TestCase):
    def setUp(self):
        self.members = [
            {"service_name": "mongo-a", "node_id": "node-1", "node_name": "host-1"},
            {"service_name": "mongo-b", "node_id": "node-1", "node_name": "host-1"},
        ]

    def test_preserves_utc_timestamp_labels_and_member_host_identity(self):
        result = range_result([[epoch(0), "12.5"]], {"node_id": "node-1", "device": "sda"})
        observations = extract_member_observations(result, self.members, "disk_iops")
        observation = observations["mongo-a"][0]
        self.assertEqual(observation["timestamp"], "2026-10-18T01:00:00Z")
        self.assertEqual(observation["value"], 12.5)
        self.assertEqual(observation["unit"], "ops/s")
        self.assertEqual(observation["node_id"], "node-1")
        self.assertEqual(observation["labels"]["device"], "sda")
        self.assertTrue(observation["host_shared"])
        self.assertEqual(observation["host_members"], ["mongo-a", "mongo-b"])

    def test_maps_host_series_by_node_name_when_prometheus_node_id_differs(self):
        member = {"service_name": "mongo-a", "node_id": "inventory-id", "address": "host-a"}
        result = range_result([[epoch(0), "42"]], {"node_name": "host-a", "node_id": "exporter-id"})
        observations = extract_member_observations(result, [member], "cpu_used_percent")
        self.assertEqual(len(observations["mongo-a"]), 1)
        self.assertEqual(observations["mongo-a"][0]["host_id"], "inventory-id")
        self.assertEqual(observations["mongo-a"][0]["labels"]["node_id"], "exporter-id")
        self.assertEqual(observations["mongo-a"][0]["node_name"], "host-a")

    def test_ambiguous_node_name_mapping_is_explicitly_shared(self):
        members = [
            {"service_name": "mongo-a", "node_id": "node-1", "address": "host-a"},
            {"service_name": "mongo-b", "node_id": "node-2", "address": "host-a"},
        ]
        observations = extract_member_observations(
            range_result([[epoch(0), "42"]], {"node_id": "old-node-id", "node_name": "host-a"}),
            members,
            "cpu_used_percent",
        )
        for service in ("mongo-a", "mongo-b"):
            self.assertEqual(len(observations[service]), 1)
            self.assertTrue(observations[service][0]["host_shared"])
            self.assertEqual(observations[service][0]["host_members"], ["mongo-a", "mongo-b"])

    def test_detects_sampled_maximum_and_deduplicates_nearby_peak_samples(self):
        result = range_result([
            [epoch(0), "1"], [epoch(1), "100"], [epoch(2), "90"],
            [epoch(12), "80"], [epoch(25), "70"], [epoch(40), "1"],
        ], {"service_name": "mongo-a"})
        peaks = detect_member_workload_peaks(result, self.members, minimum_relative_peak=1.0, dedup_window_seconds=600)
        peaks = [peak for peak in peaks if peak["service_name"] == "mongo-a"]
        self.assertEqual([peak["value"] for peak in peaks], [100.0, 80.0, 70.0])
        self.assertEqual(peaks[0]["timestamp"], epoch(1))
        self.assertTrue(peaks[0]["sampled_peak"])

    def test_alignment_respects_tolerance_missing_samples_and_scrape_cadence(self):
        ts = epoch(20)
        peaks = [{"service_name": "mongo-a", "timestamp": ts, "value": 361.13}]
        observations = {
            "operations_per_second": {"mongo-a": [
                {"timestamp_epoch": ts, "timestamp": "t0", "value": 361.13},
                {"timestamp_epoch": ts - 60, "timestamp": "t-1", "value": 50},
                {"timestamp_epoch": ts - 900, "timestamp": "base-1", "value": 10},
                {"timestamp_epoch": ts - 840, "timestamp": "base-2", "value": 10},
                {"timestamp_epoch": ts - 780, "timestamp": "base-3", "value": 10},
            ]},
            "cpu_used_percent": {"mongo-a": [
                {"timestamp_epoch": ts + 29, "timestamp": "cpu-near", "value": 35.9},
                {"timestamp_epoch": ts + 45, "timestamp": "cpu-scrape-offset", "value": 50},
                {"timestamp_epoch": ts + 120, "timestamp": "cpu-far", "value": 99},
            ]},
            "memory_used_percent": {"mongo-a": []},
        }
        event = correlate_workload_peaks(peaks, observations, self.members, 30)[0]
        cpu = event["correlated_metrics"]["cpu_used_percent"]
        self.assertEqual(cpu["event_mean"], 35.9)
        self.assertEqual(cpu["timestamps"], ["cpu-near"])
        self.assertEqual(event["correlated_metrics"]["memory_used_percent"]["status"], "unavailable")
        self.assertEqual(event["operations_baseline_mean"], 10)
        # A 90-second scrape interval widens tolerance to 45 seconds, but never matches the distant point.
        wider_cadence = correlate_workload_peaks(peaks, observations, self.members, max(60, 90) / 2)[0]
        self.assertEqual(wider_cadence["correlated_metrics"]["cpu_used_percent"]["event_samples"], 2)
        self.assertEqual(wider_cadence["correlated_metrics"]["cpu_used_percent"]["event_max"], 50)

    def test_shared_host_series_is_flagged_and_not_misrepresented_as_member_specific(self):
        ts = epoch(10)
        observations = {
            "operations_per_second": {"mongo-a": [{"timestamp_epoch": ts, "timestamp": "ops", "value": 40}],
                                      "mongo-b": [{"timestamp_epoch": ts, "timestamp": "ops", "value": 20}]},
            "cpu_used_percent": {"mongo-a": [{"timestamp_epoch": ts, "timestamp": "cpu", "value": 60,
                                               "host_id": "node-1", "host_shared": True}]},
        }
        events = correlate_workload_peaks(
            [{"service_name": "mongo-b", "timestamp": ts, "value": 20}], observations, self.members, 30
        )
        cpu = events[0]["correlated_metrics"]["cpu_used_percent"]
        self.assertEqual(cpu["event_mean"], 60)
        self.assertTrue(cpu["shared_host"])
        self.assertTrue(cpu["host_level"])

    def test_baseline_change_and_assessment_do_not_claim_causality(self):
        ts = epoch(30)
        ops = [{"timestamp_epoch": epoch(minute), "timestamp": str(minute), "value": value}
               for minute, value in [(8, 10), (10, 10), (12, 10), (15, 10), (28, 20), (30, 40), (32, 20)]]
        cpu = [{"timestamp_epoch": item["timestamp_epoch"], "timestamp": item["timestamp"], "value": 40}
               for item in ops]
        event = correlate_workload_peaks(
            [{"service_name": "mongo-a", "timestamp": ts, "value": 40}],
            {"operations_per_second": {"mongo-a": ops}, "cpu_used_percent": {"mongo-a": cpu}},
            self.members, 30,
        )[0]
        self.assertEqual(event["operations_baseline_mean"], 10)
        self.assertEqual(event["operations_event_mean"], 26.666666666666668)
        self.assertAlmostEqual(event["operations_change_percent"], 166.66666666666669)
        self.assertIn("does not establish causation", event["assessment"])
        self.assertEqual(event["missing_evidence"], [])

    def test_report_and_drilldown_overlap_is_deduplicated_for_event_and_baseline(self):
        operation_rows = []
        for minute, value in ((0, 10), (5, 11), (10, 9), (15, 20), (20, 30), (25, 22)):
            row = {
                "timestamp_epoch": epoch(minute),
                "timestamp": f"2026-10-18T01:{minute:02d}:00Z",
                "value": value,
                "labels": {"service_name": "mongo-a"},
            }
            operation_rows.extend((dict(row), dict(row)))

        events = correlate_workload_peaks(
            [{"service_name": "mongo-a", "timestamp": epoch(20), "value": 30}],
            {"operations_per_second": {"mongo-a": operation_rows}},
            [{"service_name": "mongo-a", "node_id": "node-1"}],
            alignment_tolerance_seconds=60,
        )

        event = events[0]
        self.assertEqual(event["operations_event_samples"], 3)
        self.assertEqual(event["operations_baseline_samples"], 3)
        self.assertAlmostEqual(event["operations_event_mean"], 24.0)
        self.assertAlmostEqual(event["operations_baseline_mean"], 10.0)

    def test_coarse_report_grids_use_same_bounded_detail_for_event_and_baseline(self):
        peak_ts = epoch(60)
        detail = [
            {"timestamp_epoch": peak_ts + offset * 60,
             "timestamp": f"detail-{offset}",
             "value": 80.0 if -5 <= offset <= 5 else 20.0,
             "labels": {"service_name": "mongo-a"},
             "query_source": "peak_correlation_detail"}
            for offset in range(-20, 6)
        ]
        peak = [{"service_name": "mongo-a", "timestamp": peak_ts, "value": 80.0}]
        members = [{"service_name": "mongo-a", "node_id": "node-1"}]
        event_results = []
        for coarse_step in (300, 900):
            coarse = [
                {"timestamp_epoch": peak_ts + offset * 60,
                 "timestamp": f"coarse-{coarse_step}-{offset}",
                 "value": 7.0,
                 "labels": {"service_name": "mongo-a"}}
                for offset in range(-30, 31, coarse_step // 60)
            ]
            merged = merge_peak_detail_observations(
                coarse, detail, peak_ts - 1200, peak_ts + 300,
                "memory_used_percent",
            )
            event = correlate_workload_peaks(
                peak,
                {
                    "operations_per_second": {"mongo-a": detail},
                    "memory_used_percent": {"mongo-a": merged},
                },
                members,
                alignment_tolerance_seconds=30,
            )[0]["correlated_metrics"]["memory_used_percent"]
            event_results.append(event)
        self.assertEqual(event_results[0]["event_samples"], 11)
        self.assertEqual(event_results[0]["baseline_samples"], 15)
        self.assertEqual(event_results[0]["event_mean"], event_results[1]["event_mean"])
        self.assertEqual(event_results[0]["baseline_mean"], event_results[1]["baseline_mean"])

    def test_detail_merge_does_not_interpolate_missing_observations(self):
        peak_ts = epoch(60)
        coarse = [{"timestamp_epoch": peak_ts, "timestamp": "coarse", "value": 5.0}]
        detail = [
            {"timestamp_epoch": peak_ts + offset * 60, "timestamp": str(offset), "value": 9.0}
            for offset in (-2, -1, 1, 2)
        ]
        merged = merge_peak_detail_observations(
            coarse, detail, peak_ts - 180, peak_ts + 180, "memory_used_percent"
        )
        self.assertEqual(len(merged), 4)
        self.assertNotIn(peak_ts, [row["timestamp_epoch"] for row in merged])

    def test_sparse_node_id_history_uses_richer_node_name_series_and_reports_coverage(self):
        start = "2026-10-11T18:46:59Z"
        end = "2026-10-18T18:46:59Z"
        expected = expected_range_samples(start, end, 300)
        self.assertEqual(expected, 2017)
        base = datetime.fromisoformat(start.replace("Z", "+00:00")).timestamp()
        primary = {"mongo-a": [
            {"timestamp_epoch": base + i * 300, "timestamp": "id", "value": 16.45,
             "host_id": "node-1", "labels": {"node_id": "node-1"}}
            for i in range(179)
        ]}
        fallback = {"mongo-a": [
            {"timestamp_epoch": base + i * 300, "timestamp": "name", "value": 70.57,
             "host_id": "node-1", "labels": {"node_name": "host-1"}}
            for i in range(700)
        ]}

        self.assertTrue(needs_host_label_fallback(primary, expected))
        selected, identities = prefer_more_complete_observations(primary, fallback)
        self.assertEqual(identities["mongo-a"], "node_id + node_name/address historical fallback")
        self.assertEqual(len(selected["mongo-a"]), 700)
        summary = summarize_member_observations(selected)
        add_metric_coverage(summary, selected, start, end, 300)
        self.assertEqual(summary["mongo-a"]["samples"], 700)
        self.assertEqual(summary["mongo-a"]["maximum"], 70.57)
        self.assertAlmostEqual(summary["mongo-a"]["coverage"]["coverage_percent"], 700 / 2017 * 100)
        self.assertEqual(summary["mongo-a"]["coverage"]["missing_samples"], 1317)

    def test_higher_resolution_cpu_is_reported_as_supplemental_peak_evidence(self):
        peak_timestamp = epoch(20)
        summary = {
            "status": "ok", "samples": 179, "maximum": 16.45,
            "maximum_timestamp": "2026-10-18T12:00:00Z",
            "coverage": {"expected_samples": 2017, "returned_samples": 179,
                         "coverage_percent": 8.8746, "missing_samples": 1838},
        }
        event = {"correlated_metrics": {}}
        detail = [{
            "timestamp_epoch": peak_timestamp + 120,
            "timestamp": "2026-10-18T01:22:00Z",
            "metric": "cpu_used_percent", "value": 27.31, "unit": "%",
            "host_id": "node-1", "host_shared": False,
            "node_name": "host-1", "labels": {"node_name": "host-1"},
        }]

        evidence = attach_peak_detail_evidence(
            event, "cpu_used_percent", detail, summary, "1m",
            "node_name=host-1 mapped to inventory node_id=node-1", "node-1",
            workload_peak_timestamp="2026-10-18T01:22:00Z",
        )
        self.assertEqual(summary["maximum"], 16.45)
        self.assertEqual(summary["coverage"]["returned_samples"], 179)
        self.assertEqual(evidence["maximum"], 27.31)
        self.assertEqual(evidence["maximum_timestamp"], "2026-10-18T01:22:00Z")
        self.assertEqual(evidence["comparison"], "detail_exceeds_sampled_range_maximum")
        self.assertEqual(evidence["samples"], 1)
        self.assertEqual(evidence["observations"][0]["value"], 27.31)
        self.assertEqual(evidence["at_workload_peak"]["value"], 27.31)
        self.assertIn("do not imply complete period coverage", evidence["assessment"])

    def test_peak_detail_rejects_observation_from_a_different_host(self):
        event = {"correlated_metrics": {}}
        detail = [{
            "timestamp_epoch": epoch(1), "timestamp": "wrong-host", "value": 99,
            "host_id": "node-2", "labels": {"node_name": "host-2"},
        }]
        evidence = attach_peak_detail_evidence(
            event, "cpu_used_percent", detail, {"maximum": 20, "samples": 10},
            "1m", "node_name=host-1", "node-1",
        )
        self.assertEqual(evidence["status"], "unavailable")
        self.assertIsNone(evidence["maximum"])
        self.assertEqual(evidence["observations"], [])

    def test_shared_host_cpu_is_mapped_to_members_but_explicitly_marked_shared(self):
        members = [
            {"service_name": "mongo-a", "node_id": "node-1", "address": "host-1"},
            {"service_name": "mongo-b", "node_id": "node-1", "address": "host-1"},
        ]
        observations = extract_member_observations(
            range_result([[epoch(0), "27.31"]], {"node_id": "node-1"}),
            members,
            "cpu_used_percent",
        )
        summary = summarize_member_observations(observations)
        self.assertEqual(summary["mongo-a"]["maximum"], 27.31)
        self.assertEqual(summary["mongo-b"]["maximum"], 27.31)
        self.assertTrue(summary["mongo-a"]["shared_host"])
        self.assertTrue(summary["mongo-b"]["shared_host"])
        self.assertEqual(summary["mongo-a"]["host_members"], ["mongo-a", "mongo-b"])

    def test_container_exporters_with_matching_kernel_and_device_signature_share_host_group(self):
        members = [
            {"service_name": "mongo-a", "node_id": "node-1", "address": "host-a"},
            {"service_name": "mongo-b", "node_id": "node-2", "address": "host-b"},
            {"service_name": "mongo-c", "node_id": "node-3", "address": "host-c"},
        ]
        boot = {"data": {"result": [
            {"metric": {"node_id": "node-1", "node_name": "host-a", "node_type": "container"}, "value": [1, "1000"]},
            {"metric": {"node_id": "node-2", "node_name": "host-b", "node_type": "container"}, "value": [1, "1000"]},
            {"metric": {"node_id": "node-3", "node_name": "host-c", "node_type": "container"}, "value": [1, "1000"]},
        ]}}
        disks = {"data": {"result": [
            {"metric": {"node_id": node, "node_name": host, "device": "nvme0n1", "major": "259", "minor": "0"}, "value": [1, "1"]}
            for node, host in (("node-1", "host-a"), ("node-2", "host-b"), ("node-3", "host-c"))
        ]}}
        topology = infer_host_topology(members, boot, disks)
        self.assertEqual(len(topology["groups"]), 1)
        self.assertTrue(topology["groups"][0]["shared"])
        self.assertIn("container_kernel_signature", topology["groups"][0]["identity_method"])

    def test_similar_devices_on_independent_container_hosts_do_not_merge(self):
        members = [
            {"service_name": "mongo-a", "node_id": "node-1", "address": "host-a"},
            {"service_name": "mongo-b", "node_id": "node-2", "address": "host-b"},
        ]
        boot = {"data": {"result": [
            {"metric": {"node_id": "node-1", "node_name": "host-a", "node_type": "container"}, "value": [1, "1000"]},
            {"metric": {"node_id": "node-2", "node_name": "host-b", "node_type": "container"}, "value": [1, "2000"]},
        ]}}
        disks = {"data": {"result": [
            {"metric": {"node_id": node, "node_name": host, "device": "nvme0n1", "major": "259", "minor": "0"}, "value": [1, "1"]}
            for node, host in (("node-1", "host-a"), ("node-2", "host-b"))
        ]}}
        topology = infer_host_topology(members, boot, disks)
        self.assertEqual(len(topology["groups"]), 2)
        self.assertTrue(all(not group["shared"] for group in topology["groups"]))
        self.assertTrue(all(group["physical_identity_confidence"] == "physical_host_unverified"
                            for group in topology["groups"]))

    def test_shared_host_resource_rows_are_stored_once_and_summaries_are_marked_shared(self):
        members = [
            {"service_name": "mongo-a", "node_id": "node-1", "address": "host-a"},
            {"service_name": "mongo-b", "node_id": "node-2", "address": "host-b"},
        ]
        boot = {"data": {"result": [
            {"metric": {"node_id": "node-1", "node_name": "host-a", "node_type": "container"}, "value": [1, "1000"]},
            {"metric": {"node_id": "node-2", "node_name": "host-b", "node_type": "container"}, "value": [1, "1000"]},
        ]}}
        disks = {"data": {"result": [
            {"metric": {"node_id": node, "node_name": host, "device": "nvme0n1", "major": "259", "minor": "0"}, "value": [1, "1"]}
            for node, host in (("node-1", "host-a"), ("node-2", "host-b"))
        ]}}
        topology = infer_host_topology(members, boot, disks)
        start = "2026-10-18T00:00:00Z"
        end = "2026-10-18T00:10:00Z"
        observations = {"cpu_used_percent": {
            "mongo-a": [{"timestamp_epoch": epoch(0), "timestamp": "a", "value": 20,
                          "labels": {"node_id": "node-1", "instance": "a"}, "source_matches_inventory_node_id": True}],
            "mongo-b": [{"timestamp_epoch": epoch(0), "timestamp": "b", "value": 21,
                          "labels": {"node_id": "node-2", "instance": "b"}, "source_matches_inventory_node_id": True}],
        }}
        summaries = {"cpu_used_percent": summarize_member_observations(observations["cpu_used_percent"])}
        apply_host_topology(summaries, observations, topology, start, end, 300)
        self.assertEqual(sum(bool(rows) for rows in observations["cpu_used_percent"].values()), 1)
        self.assertTrue(summaries["cpu_used_percent"]["mongo-a"]["shared_host"])
        self.assertTrue(summaries["cpu_used_percent"]["mongo-b"]["shared_host"])
        self.assertEqual(summaries["cpu_used_percent"]["mongo-a"]["maximum"],
                         summaries["cpu_used_percent"]["mongo-b"]["maximum"])

    def test_historical_role_changes_are_resolved_at_each_peak_timestamp(self):
        rows = [
            {"timestamp_epoch": epoch(4), "timestamp": "before", "value": 2},
            {"timestamp_epoch": epoch(5), "timestamp": "after", "value": 1},
        ]
        self.assertEqual(historical_role_at_timestamp(rows, epoch(4), 10)["status"], "secondary")
        self.assertEqual(historical_role_at_timestamp(rows, epoch(5), 10)["status"], "primary")
        self.assertEqual(historical_role_at_timestamp(rows, epoch(20), 10)["status"], "unknown")

    def test_peak_detection_deduplicates_overlapping_exporter_identities_per_timestamp(self):
        result = {"data": {"result": [
            {"metric": {"service_name": "mongo-a", "node_id": "old-id", "instance": "old"},
             "values": [[epoch(0), "99"], [epoch(1), "99"], [epoch(2), "99"]]},
            {"metric": {"service_name": "mongo-a", "node_id": "node-1", "instance": "new"},
             "values": [[epoch(0), "10"], [epoch(1), "11"], [epoch(2), "12"]]},
        ]}}

        peaks = detect_member_workload_peaks(result, self.members[:1])

        self.assertEqual(len(peaks), 1)
        self.assertEqual(peaks[0]["value"], 12.0)
        self.assertAlmostEqual(peaks[0]["mean"], 11.0)

    def test_refines_coarse_peak_to_exact_detail_timestamp_without_fabricating_samples(self):
        coarse_timestamp = epoch(71)  # 02:11 UTC; outer 5m/15m evaluation
        peak = {"service_name": "mongo-a", "timestamp": coarse_timestamp, "value": 361.13}
        details = {"mongo-a": [
            {"timestamp_epoch": epoch(67), "timestamp": "2026-10-18T02:07:00Z", "value": 361.11,
             "labels": {"node_name": "host-1"}},
            {"timestamp_epoch": epoch(68), "timestamp": "2026-10-18T02:08:00Z", "value": 361.13,
             "labels": {"node_name": "host-1"}},
        ]}

        refined = refine_workload_peak_timestamps([peak], details, 15 * 60)[0]
        unavailable = refine_workload_peak_timestamps([peak], {}, 15 * 60)[0]

        self.assertEqual(refined["timestamp"], epoch(68))
        self.assertEqual(refined["value"], 361.13)
        self.assertTrue(refined["timestamp_refined"])
        self.assertEqual(refined["detection_timestamp"], coarse_timestamp)
        self.assertFalse(unavailable["timestamp_refined"])
        self.assertEqual(unavailable["timestamp"], coarse_timestamp)
        self.assertEqual(unavailable["value"], 361.13)

    def test_seven_ten_and_thirty_day_discovery_grids_find_burst_missed_by_coarse_summary(self):
        from pmm_mcp.server import _workload_peak_discovery_query

        base_query = (
            'sum by (service_name,node_id,node_name,instance,job,agent_id,service_id) '
            '(rate(mongodb_op_counters_total{service_name="mflix-rs"}[5m]))'
        )
        member = [{"service_name": "mflix-rs", "node_id": "current-node-id", "node_name": "mongodb7-rs1"}]
        detail_peak_timestamp = datetime.fromisoformat("2026-10-18T02:06:23.776+00:00").timestamp()
        detailed_rows = [
            {"timestamp_epoch": detail_peak_timestamp + offset * 60,
             "timestamp": datetime.fromtimestamp(detail_peak_timestamp + offset * 60, UTC).isoformat().replace("+00:00", "Z"),
             "value": value,
             "labels": {"service_name": "mflix-rs", "node_id": "historical-node-id"}}
            for offset, value in ((-2, 361.11), (-1, 361.126), (0, 361.133), (1, 361.113), (2, 361.08))
        ]

        for period_hours, report_step, candidate_time in (
            (24 * 7, "5m", datetime.fromisoformat("2026-10-18T02:09:53.776+00:00").timestamp()),
            (24 * 10, "15m", datetime.fromisoformat("2026-10-18T02:15:18.984+00:00").timestamp()),
            (24 * 30, "15m", datetime.fromisoformat("2026-10-18T02:15:18.984+00:00").timestamp()),
        ):
            self.assertEqual(select_report_step(period_hours), report_step)
            query, detail = _workload_peak_discovery_query(base_query, report_step, None)
            self.assertIsNotNone(query)
            self.assertEqual(detail["status"], "planned")
            self.assertEqual(detail["subquery_step"], "1m")
            self.assertIn(f"[{report_step}:1m]", query)

            # This is representative of the 15-minute report grid that omitted
            # the October 18 burst: its sampled max is below the old 1.5x-mean
            # drilldown threshold.
            coarse_values = [[candidate_time - 600, "12"],
                             [candidate_time, "16.75"],
                             [candidate_time + 600, "13"]]
            coarse_mean = sum(float(point[1]) for point in coarse_values) / len(coarse_values)
            self.assertLess(max(float(point[1]) for point in coarse_values), coarse_mean * 1.5)

            # The bounded max_over_time screen finds the short event at the
            # coarse interval's evaluation timestamp, even with a historical
            # exporter node_id that differs from today's inventory identity.
            discovery = {"data": {"result": [{
                "metric": {"service_name": "mflix-rs", "node_id": "historical-node-id",
                           "node_name": "mongodb7-rs1", "instance": "old-agent", "job": "mongo_exporter"},
                "values": [[candidate_time - 600, "13"], [candidate_time, "361.13"],
                           [candidate_time + 600, "14"]],
            }]}}
            peaks = detect_member_workload_peaks(
                discovery,
                member,
                dedup_window_seconds=max(600, 2 * int(parse_prometheus_duration(report_step))),
            )
            refined = refine_workload_peak_timestamps(
                peaks,
                {"mflix-rs": detailed_rows},
                300 if report_step == "5m" else 900,
            )
            event = next(item for item in refined if item["service_name"] == "mflix-rs")
            self.assertEqual(event["timestamp"], detail_peak_timestamp)
            self.assertAlmostEqual(event["value"], 361.133)
            self.assertTrue(event["timestamp_refined"])
            self.assertEqual(event["detection_timestamp"], candidate_time)


if __name__ == "__main__":
    unittest.main()
