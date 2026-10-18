# MongoDB and PMM documentation used by this Skill

Access date: 2026-10-17. These references describe database/monitoring semantics; **not evidence about any customer**. Product behavior can change across versions, so confirm the relevant server and PMM release before applying guidance.

## MongoDB official
1. WiredTiger storage engine: https://www.mongodb.com/docs/manual/core/wiredtiger/
   - Internal cache and OS filesystem cache; default cache behavior; cautions about manual size changes; dynamic ticket behavior in modern releases.
2. `serverStatus` reference: https://www.mongodb.com/docs/manual/reference/command/serverStatus/
   - `opcounters`, `metrics.document`, WiredTiger cache/transaction stats, flow control, concurrency; available fields vary by release. Confirm actual field names.
3. MongoDB 7.0 `serverStatus`: https://www.mongodb.com/docs/v7.0/reference/command/serverStatus/
   - Dynamic read/write ticket adjustment; long queues are more meaningful than zero available tickets. Flow-control primary semantics.
4. Query `explain` statistics: https://www.mongodb.com/docs/manual/reference/explain-results/
   - `nReturned`, `totalDocsExamined`, `totalKeysExamined`, plan stages, limitations of measured execution time.

## Percona PMM documentation
1. MongoDB Instance Summary: https://docs.percona.com/percona-monitoring-and-management/3/reference/dashboards/dashboard-mongodb-instance-summary.html
2. MongoDB WiredTiger Details: https://docs.percona.com/percona-monitoring-and-management/3/reference/dashboards/dashboard-mongodb-wiredtiger-details.html
3. MongoDB ReplSet Summary: https://docs.percona.com/percona-monitoring-and-management/3/reference/dashboards/dashboard-mongodb-replset-summary.html
4. MongoDB Oplog Details: https://docs.percona.com/percona-monitoring-and-management/3/reference/dashboards/dashboard-mongodb-oplog-details.html
5. MongoDB Cluster Summary: https://docs.percona.com/percona-monitoring-and-management/3/reference/dashboards/dashboard-mongodb-cluster-summary.html
6. MongoDB stored QAN metrics: https://docs.percona.com/percona-monitoring-and-management/3/use/qan/qan_mongo.html
7. Stored QAN overview and availability: https://docs.percona.com/percona-monitoring-and-management/3/use/qan/QAN-stored-metrics.html
8. Dashboard catalog: https://docs.percona.com/percona-monitoring-and-management/3/use/dashboards-panels/index.html

## Required decision rules derived from these docs
- High WiredTiger cache use is not alone proof of a bottleneck; examine eviction, dirty cache, OS memory and I/O before suggesting cache tuning. MongoDB warns against increasing its internal cache above defaults casually.
- MongoDB >= 7.0 dynamically manages WiredTiger concurrency. Persistent queues with impact warrant investigation; `available=0` alone does **not** prove saturation.
- `opcounters` are operation counters, not document mutation totals: MongoDB's `metrics.document` has different semantics.
- Explain plans provide scanned/returned signals useful for query diagnosis; QAN itself requires collection/coverage context; do not assert that a specific query is slow without query-level data.
- Flow control is relevant on the primary and may not engage under all forms of replication lag.
- Oplog window relates to recovery headroom; compare to **known** lag, backup/maintenance durations where available rather than blindly applying global thresholds.
- PMM panels evolve. Always discover metrics available from actual PMM before analysis, rather than presuming fixed metric names.

## Methodological inspiration (not an operational rulebook)
- Guy Harrison and Michael Harrison, *MongoDB Performance Tuning: Optimizing MongoDB Databases and Their Applications* (Apress, 2021), publisher outline: https://link.springer.com/book/10.1007/978-1-4842-6879-7
  - The publisher describes a methodical, top-down approach addressing foundational application, schema, query, and index inefficiencies before server/hardware/cluster tuning. The Skill's analysis framework is independently authored; validate all technical conclusions against current official MongoDB documentation.
