# Percona Managed Services Assessment

**Cluster:** {{ cluster }}<br>
**Replica set:** {{ replica_set }}<br>
**Review period:** {{ period }}<br>
**Generated:** {{ generated_at }}

## 1. Executive summary

{{ observed_condition }}

**Percona assessment:** {{ assessment }}<br>
**Customer relevance:** {{ customer_relevance }}<br>
**Suggested focus:** {{ suggested_focus }}

## 2. Cluster identity and topology

| Service | Node | MongoDB version | Role | Role observed at | Agent status |
| --- | --- | --- | --- | --- | --- |
| {{ service_name }} | {{ node_name }} | {{ version }} | {{ role_or_unknown }} | {{ role_observed_at }} | {{ agent_status }} |

**Topology summary:** {{ topology_summary }}

**Consistency checks:** {{ consistency_check_status }}<br>
{{ consistency_warnings }}

## 3. Resources and capacity

{{ cpu_and_filesystem_findings }}

## 4. MongoDB storage engine health

{{ wiredtiger_findings }}

## 5. Workload and concurrency

{{ operations_and_connections_findings }}

## 6. Replication and cluster health

{{ replication_findings }}

## 7. Operational events and alerts

{{ event_data_availability }}

## 8. Recommended follow-up

{{ evidence_led_recommendations }}

## 9. Data sources and limitations

{{ data_sources }}

{{ limitations }}

> This performance assessment summarizes available PMM observations. It does not replace the standard health check or declare the service healthy.
