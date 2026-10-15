import unittest

from pmm_mcp.dashboard_catalog import extract_metric_names


class DashboardCatalogTests(unittest.TestCase):
    def test_extracts_selectors_and_bare_metrics_without_promql_functions(self) -> None:
        expression = (
            'avg_over_time(mongodb_up{service_name=~"$service_name"}[5m]) '
            'or node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes '
            'or vector(0)'
        )
        self.assertEqual(
            extract_metric_names(expression),
            [
                "mongodb_up",
                "node_memory_MemAvailable_bytes",
                "node_memory_MemTotal_bytes",
            ],
        )

    def test_ignores_variables_and_label_names(self) -> None:
        expression = 'sum by (node_name) (rate(node_cpu_seconds_total{mode!="idle"}[5m]))'
        self.assertEqual(extract_metric_names(expression), ["node_cpu_seconds_total"])

    def test_ignores_case_insensitive_promql_operators(self) -> None:
        self.assertEqual(extract_metric_names("time() OR mongodb_up{}"), ["mongodb_up"])


if __name__ == "__main__":
    unittest.main()
