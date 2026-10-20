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
    needs_host_label_fallback,
    prefer_more_complete_observations,
    summarize_member_observations,
)


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


if __name__ == "__main__":
    unittest.main()
