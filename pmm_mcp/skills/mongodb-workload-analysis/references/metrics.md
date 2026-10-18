# PMM metrics used by the assessment

The report summarizes available PMM range samples by inventoried `service_name`. It does not fill gaps. A missing series is reported as unavailable, not as zero or normal behavior.

| Report field | Interpretation |
| --- | --- |
| `cpu_used_percent` | Observed CPU utilization percentage; compare peaks with workload and capacity objectives. |
| `root_filesystem_used_percent` | Root filesystem utilization; consider growth rate and available expansion time. |
| `connection_used_percent` | Connection use relative to the configured/observed capacity basis. |
| `wiredtiger_dirty_page_percent` | Dirty pages as a percentage of the configured WiredTiger cache basis; interpret with eviction, queues, tickets, and latency. |
| `operations_per_second` | Operation rate; interpret with workload shape and latency rather than as a standalone capacity score. |
| `replication_lag_seconds` | Observed member lag; distinguish intentionally delayed members and transient samples. |
| `mongodb_mongod_replset_my_state` | Current MongoDB member state gauge used to identify each member's role. |

Range summaries expose sample count, latest sample, mean, and maximum. A maximum is a sampled peak, not a continuous guarantee. Review `metric_sources`, `limitations`, and `executive_summary.unavailable_metric_families` alongside every value.
