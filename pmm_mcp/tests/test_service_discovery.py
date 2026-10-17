import unittest

from pmm_mcp.service_discovery import merge_mongodb_inventory, mongodb_services, parse_labels


SAMPLE = """
<h4><span>mongodb_exporter_abc (1/1 active)</span><button>collapse</button></h4>
<div class="scrape-job"><table><tbody>
<tr><td><span>UP</span></td>
<td class="labels">{__address__=&quot;mongodb8-mcp:42001&quot;, __metrics_path__=&quot;/metrics&quot;, service_id=&quot;svc-1&quot;, service_name=&quot;mongodb8-mcp&quot;, service_type=&quot;mongodb&quot;, node_name=&quot;mongodb8-mcp&quot;}</td>
<td class="labels">{service_id=&quot;svc-1&quot;, service_name=&quot;mongodb8-mcp&quot;, service_type=&quot;mongodb&quot;, node_name=&quot;mongodb8-mcp&quot;}</td></tr>
</tbody></table></div>
<h4><span>mysql_exporter_def (1/1 active)</span></h4>
<div class="scrape-job"><table><tbody>
<tr><td>UP</td><td class="labels">{service_type=&quot;mysql&quot;}</td><td class="labels">{service_type=&quot;mysql&quot;}</td></tr>
</tbody></table></div>
"""


class ServiceDiscoveryTests(unittest.TestCase):
    def test_parse_labels(self) -> None:
        self.assertEqual(
            parse_labels('{job="mongodb", port="27017"}'),
            {"job": "mongodb", "port": "27017"},
        )

    def test_filters_only_mongodb(self) -> None:
        services = mongodb_services(SAMPLE)
        self.assertEqual(len(services), 1)
        self.assertEqual(services[0]["service_name"], "mongodb8-mcp")
        self.assertEqual(services[0]["service_type"], "mongodb")
        self.assertEqual(services[0]["status"], "UP")

    def test_inventory_is_identity_when_metrics_are_push(self) -> None:
        services = merge_mongodb_inventory(
            {
                "mongodb": [
                    {
                        "service_id": "svc-1",
                        "service_name": "mongodb8-mcp",
                        "address": "mongodb8-mcp",
                        "port": 27017,
                        "node_id": "node-1",
                    }
                ]
            },
            {
                "mongodb_exporter": [
                    {
                        "service_id": "svc-1",
                        "agent_id": "agent-1",
                        "push_metrics_enabled": True,
                        "status": "AGENT_STATUS_RUNNING",
                        "process_exec_path": "/mongodb_exporter",
                    }
                ]
            },
            [],
        )
        self.assertEqual(services[0]["service_id"], "svc-1")
        self.assertEqual(services[0]["agents"][0]["metrics_mode"], "push")
        self.assertEqual(services[0]["service_discovery_targets"], [])


if __name__ == "__main__":
    unittest.main()
