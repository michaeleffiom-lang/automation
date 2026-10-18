# PMM metrics used by the assessment

The report summarizes available PMM range samples by inventoried member. MongoDB exporter series normally map by `service_name`; node-exporter series map by the inventory `node_id`, or by node name when an ID is unavailable. The report does not fill gaps. A missing series is reported as unavailable, not as zero or normal behavior.

| Report field | Interpretation |
| --- | --- |
| `cpu_used_percent` | Observed CPU utilization percentage from `node_cpu_seconds_total`, joined to the member by node identity; compare peaks with workload and capacity objectives. |
| `root_filesystem_used_percent` | Utilization of `node_exporter` mountpoint `/`, joined by node identity; a container overlay is not physical host disk capacity. |
| `connection_used_percent` | Connection use relative to the configured/observed capacity basis. |
| `wiredtiger_dirty_page_percent` | `mongodb_ss_wt_cache_tracked_dirty_bytes_in_the_cache` as a percentage of `mongodb_mongod_wiredtiger_cache_max_bytes`; interpret with eviction, queues, tickets, and latency. |
| `operations_per_second` | Operation rate; interpret with workload shape and latency rather than as a standalone capacity score. |
| `replication_lag_seconds` | Observed member lag; distinguish intentionally delayed members and transient samples. |
| `mongodb_mongod_replset_my_state` | Current MongoDB member state gauge used to identify each member's role. |

Range summaries expose sample count, latest sample, mean, and maximum. A maximum is a sampled peak, not a continuous guarantee. Review `metric_sources`, `limitations`, and `executive_summary.unavailable_metric_families` alongside every value.
