# MongoDB Sharded Cluster PMM Operational Analyzer

## Overview

The **MongoDB Sharded Cluster PMM Operational Analyzer** is an n8n workflow that produces a 7-day technical operational review of a MongoDB sharded cluster using metrics already available in **Percona Monitoring and Management (PMM)**.

The workflow is designed to answer questions such as:

- What MongoDB components belong to the selected sharded cluster?
- Which replica sets are confirmed data shards?
- How is observed workload distributed across shard PRIMARY members?
- How are chunks and current database-level `dbStats` footprints distributed?
- What replication lag and oplog context is visible?
- How do WiredTiger and host-resource signals differ across members?
- What is the current balancer state?
- Was modeled `moveChunk` or range-deleter activity observed during the review period?
- Are there important monitoring gaps or data-quality limitations?
- What should a DBA investigate next?

The analyzer follows a deliberately conservative principle:

> **Characterize aggressively; diagnose conservatively.**

It reports what the metrics show, but avoids turning metric asymmetry or temporal coincidence into unsupported root-cause conclusions.

---

## Current Workflow

**Workflow:** `Automated 7 Day MongoDB Sharded Cluster PMM Operational Analyzer v12 - Clean Batch Scope`

The workflow is designed for **n8n 2.8.x self-hosted** and uses PMM's Grafana/Prometheus datasource API.

The default configuration in the workflow is:

| Setting | Default |
|---|---|
| PMM URL | `https://127.0.0.1:18000` |
| Prometheus datasource UID | `PA58DA793C7250F1B` |
| Prometheus datasource ID | `1` |
| Review period | `7 days` |
| Nominal sampling interval | `5m` |
| Maximum data points | `1200` |
| Report timezone | `UTC` |

These values are controlled in the **PMM Connection Config** node.

---

# How It Works

## High-Level Architecture

The workflow intentionally does **not** load every seven-day MongoDB time series from the entire cluster into one n8n Code node.

Instead, it discovers the cluster first and then analyzes separate logical batches:

```text
User starts workflow
        |
        v
Discover MongoDB services and mongos routers
        |
        v
User selects a mongos anchor
        |
        v
Discover shard membership
        |
        v
Build exact cluster service list
        |
        v
Discover available PMM metrics
        |
        v
Build analysis batches
        |
        +--> Shard replica set 0
        |
        +--> Shard replica set 1
        |
        +--> Shard replica set N
        |
        +--> mongos routing layer
        |
        +--> Config server replica set
        |    (only when explicitly identified)
        |
        +--> Cluster/sharding metadata
        |
        v
Analyze each batch independently
        |
        v
Discard raw time-series payloads
        |
        v
Merge compact statistical summaries
        |
        v
Data-quality validation
        |
        v
Deterministic + optional Gemini interpretation
        |
        v
DBA-review-required report
```

This batching architecture keeps n8n from having to process the entire cluster's raw seven-day metric payload inside one Code node.

---

# 1. Cluster Discovery

## Mongos-Anchored Discovery

The workflow does **not** assume that the PMM `cluster` label represents a MongoDB sharded-cluster boundary.

It also does not require exporter `cl_id`, because some environments do not populate it.

Instead, cluster discovery is anchored on a **mongos service**.

The workflow first queries lightweight topology metrics such as:

```promql
mongodb_up
```

and:

```promql
mongodb_mongod_replset_my_state
```

The selector identifies services whose exporter role indicates `mongos`.

The user then selects one mongos router as the cluster anchor.

### Why mongos is used as the anchor

A mongos router has knowledge of the configured shards. PMM's MongoDB exporter can expose that relationship through:

```promql
mongodb_shards_collection_chunks_count
```

The workflow reads the `shard` label from this metric to identify actual data-shard replica-set names.

For example:

```text
mongos-0
   |
   +-- dev-shard0
   +-- dev-shard1
   +-- dev-shard2
```

This is stronger evidence than guessing from hostnames or replica-set names.

---

# 2. Shard Classification

A replica set is classified as a **data shard** only when its replica-set name appears as a `shard` label in PMM sharding metrics.

The workflow does not classify a replica set as a shard merely because its name contains text such as:

```text
shard0
shard1
shard2
```

Likewise, a replica set that is not present in the shard list is **not automatically assumed to be the config-server replica set**.

This avoids incorrect topology inference.

---

# 3. Config Server Handling

Config-server members are included only when PMM/exporter metadata explicitly identifies their role as a config server, for example through a role equivalent to:

```text
configsvr
config server
```

If the workflow cannot explicitly identify the config-server replica set, the report states that:

> Config-server replica-set health is outside the observed topology coverage.

It does not guess which replica set is the config server.

---

# 4. Metric Discovery

After the cluster is assembled, the workflow discovers which MongoDB and node-exporter metrics are actually available in PMM.

This allows the workflow to adapt to differences between:

- MongoDB versions
- MongoDB exporter versions
- PMM versions
- enabled exporter collectors
- monitored topology
- available node-exporter metrics

Unknown sharding-related metric names are retained as **capability evidence**, but are not automatically assigned semantics.

The workflow deliberately avoids querying every discovered metric over seven days.

Only explicitly modeled metrics are selected for analysis.

---

# 5. Batched Execution

The **Build Analysis Batches** node partitions the selected queries into logical groups.

## Data-Shard Replica-Set Batches

Each confirmed data-shard replica set gets its own analysis batch.

Typical categories include:

- topology
- workload
- operation latency
- connections
- network
- replication
- WiredTiger
- host resources

Example:

```text
dev-shard0
  s0-r0 PRIMARY
  s0-r1 SECONDARY
  s0-r2 SECONDARY
```

is processed independently from:

```text
dev-shard1
```

and:

```text
dev-shard2
```

## Mongos Batch

All selected mongos routers are analyzed as a separate routing-layer batch.

The mongos batch includes applicable metrics such as:

- operation rates
- operation latency
- connections
- MongoDB network counters
- host resource context

Replica-set member-state and replication metrics are explicitly excluded from the mongos batch.

## Cluster Metadata Batch

Low-cardinality sharding-specific metrics are handled separately, including:

- chunk distribution
- sharded namespace count
- balancer enabled state
- balancer running state
- current shard count
- draining shard count
- modeled moveChunk counter increases
- range-deleter activity
- unfinished migration state
- current per-shard `dbStats` footprint

## Config Server Batch

If config-server services are explicitly identified, they receive their own replica-set analysis batch.

---

# 6. Why the Workflow Uses Batches

A seven-day review can contain a large number of samples.

At a nominal five-minute interval:

```text
7 days x 24 hours x 12 samples/hour
= 2,016 possible samples per series
```

A sharded cluster can expose many series across multiple members and labels.

Processing all of that in one synchronous Code node can cause n8n's task runner to become unresponsive.

The workflow therefore follows this model:

```text
Raw PMM series
    |
    v
Batch-specific statistics
    |
    v
Compact summary
    |
    X raw points discarded
    |
    v
Cluster-level aggregation
```

Only the compact summaries cross the cluster aggregation boundary.

---

# What the Analyzer Measures

## Topology

The workflow identifies:

- mongos routers
- replica-set names
- current PRIMARY members
- current SECONDARY members
- ARBITER members when present
- confirmed data-shard replica sets
- explicitly identified config-server services

MongoDB replica-set state codes are translated into states such as:

| Code | State |
|---:|---|
| 0 | STARTUP |
| 1 | PRIMARY |
| 2 | SECONDARY |
| 3 | RECOVERING |
| 5 | STARTUP2 |
| 6 | UNKNOWN |
| 7 | ARBITER |
| 8 | DOWN |
| 9 | ROLLBACK |
| 10 | REMOVED |

A topology state is not automatically treated as a complete health assessment.

---

## Workload Distribution

The workflow uses MongoDB operation-latency counters to derive observed average operation rates where the required metrics are available.

It analyzes categories such as:

- reads
- writes
- commands

For data shards, workload comparison is performed primarily across the **current PRIMARY members**.

The report can calculate the observed share of shard-PRIMARY read and write rates.

Example:

```text
Shard        Read-rate share    Write-rate share
dev-shard0          7%                  8%
dev-shard1          5%                  0%
dev-shard2         88%                 92%
```

These are **distribution indicators**, not exact application-operation shares.

The workflow does not conclude that concentrated traffic automatically means:

- bad shard key
- hot shard root cause
- incorrect routing
- application defect

Those conclusions require additional sharding and query evidence.

---

## Operation Latency

When the required PMM metrics exist, the workflow derives operation latency for:

- reads
- writes
- commands

Statistics can include:

- minimum
- average
- P50
- P95
- P99
- maximum

There is no universal latency threshold built into the workflow.

The report therefore avoids automatically classifying a numeric latency as good, bad, high, low, or problematic without an SLA or baseline.

---

## Replication

The workflow analyzes available replication signals including:

- replica-set member state
- replication lag
- oplog window

Replication-lag data can be exposed from multiple exporter perspectives. The workflow maps the observed target member back to its replica set and conservatively deduplicates duplicate perspectives.

The report keeps:

```text
average
P99
maximum
```

as separate observations.

It does not infer the cause of a lag event from lag alone.

The oplog window is treated as **replication/catch-up context**, not proof of:

- backup coverage
- PITR
- durability
- acceptable RPO
- acceptable RTO
- sufficient recovery headroom

---

## WiredTiger

The workflow analyzes available WiredTiger metrics such as:

- cache utilization
- dirty-cache percentage

These are contextual signals.

For example, an 80% cache-occupancy value is not automatically interpreted as:

- cache pressure
- an oversized working set
- insufficient RAM
- a need to resize WiredTiger cache

Additional evidence would be required.

---

## Connections

The workflow can report:

- current MongoDB connections
- available connection capacity
- calculated connection-utilization percentage

Connection utilization is not assigned a universal good/bad threshold.

---

## MongoDB Network Activity

Where available, the workflow analyzes:

- bytes received per second
- bytes sent per second
- requests per second

Network counters describe MongoDB service traffic.

They do not identify:

- application payload composition
- query targeting quality
- scatter/gather behavior
- client read preference
- routing policy

---

# Sharding-Specific Analysis

## Chunk Distribution

The workflow uses:

```promql
mongodb_shards_collection_chunks_count
```

to calculate current chunk counts by shard.

Identical chunk metadata exposed by multiple mongos routers is deduplicated before shard totals are calculated.

The workflow can report:

```text
Shard        Chunks        Share
Shard A        4            10%
Shard B        3             8%
Shard C       32            82%
```

Chunk count is **not equivalent to data size**.

Uneven chunk counts do not automatically prove:

- bad shard key
- data imbalance requiring remediation
- failed balancer
- hotspot cause

---

## Sharded Namespace Count

The workflow calculates a current count of database/collection namespaces represented by the shard chunk metric.

This is a topology/distribution indicator only.

---

## Per-Shard dbStats Footprint

If PMM exposes the required raw shard `dbStats` metrics, the workflow builds a current database-level footprint for each shard.

It can include:

- `dataSize`
- `storageSize`
- `indexSize`
- object count

Example:

```text
Shard        dataSize      storageSize      indexSize
shard0       ...
shard1       ...
shard2       ...
```

These values are useful for comparing current per-shard database footprints.

They are **not** direct measurements of:

- filesystem utilization
- data growth
- balance of only sharded collections
- shard-key quality

The values may also reflect database-level placement, including data that should not be interpreted as a direct sharded-collection distribution measurement.

---

## Multi-Signal Concentration

One of the workflow's most useful capabilities is combining independent distribution observations.

If the same shard leads in:

- chunk-count share
- current `dbStats dataSize` share
- observed shard-PRIMARY read-rate share
- observed shard-PRIMARY write-rate share

the report can characterize this as:

> **Multi-signal shard concentration**

This is stronger evidence of concentration than any single metric.

However, it still does not establish the reason.

Possible explanations that require further investigation may include:

- shard-key/range distribution
- zone configuration
- primary-shard placement of unsharded data
- application access patterns
- collection-specific behavior

The workflow does not choose among those explanations without evidence.

---

# Balancer and Migration Signals

## Balancer State

Where available, the workflow captures:

- balancer enabled state
- balancer running state

These are current snapshots.

A value indicating that the balancer is enabled or running does not prove:

- migrations are occurring
- migrations are succeeding
- the cluster is balanced
- the balancer is effective

---

## moveChunk Activity

The workflow can calculate seven-day increases for modeled serverStatus sharding counters such as:

- donor moveChunk started
- donor moveChunk committed
- donor moveChunk aborted

These are sums of observed member-local counter increases.

They are not asserted to equal an exact number of unique logical chunk migrations.

A zero increase means only that the modeled counters did not show an increase during the review period.

It does not automatically mean that the balancer is:

- stuck
- failing
- inactive
- unable to address a distribution

---

## Range-Deleter Activity

The workflow can analyze:

- current range-deleter tasks
- seven-day document-deletion counter increase
- seven-day byte-deletion counter increase
- `unfinishedMigrationFromPreviousPrimary`

Range-deleter activity can be normal after chunk movement.

Its presence is not inherently a fault.

---

## Jumbo Chunks

The workflow does not use command counters such as `clearJumboFlag` as proof that jumbo chunks currently exist.

Jumbo-chunk state is reported only when a dedicated state/count metric is available.

---

# Host Resource Context

Where node-exporter metrics are available, the workflow analyzes member-level host context such as:

- logical CPU count
- CPU utilization
- memory utilization based on available-memory metrics
- busiest-device disk busy percentage
- highest filesystem-used percentage

These are supporting observations.

The workflow deliberately avoids conclusions such as:

- resize the node
- add RAM
- storage is saturated
- CPU is overloaded

based only on utilization percentages.

`disk busy %` is treated as **device busy-time percentage**, not:

- IOPS
- throughput
- latency

---

# Data Quality

Before report generation, the workflow runs a dedicated **Data Quality Gate**.

It checks:

- whether modeled metrics were available
- whether valid numeric PMM series were returned
- sampling completeness
- missing intervals
- query/extraction errors
- expected cluster services
- expected shard batches
- whether mongos and shard membership were preserved

Possible statuses include:

```text
reliable
usable_with_caution
needs_validation
not_reliable
```

## maxDataPoints-Aware Completeness

The workflow deliberately limits seven-day query resolution with:

```text
max_data_points = 1200
```

A seven-day range at a nominal five-minute resolution can contain approximately 2,016 samples.

Therefore the analyzer calculates an **effective expected interval** that accounts for the `maxDataPoints` limit.

Intentional Grafana/Prometheus query downsampling is not incorrectly counted as missing scrape data.

---

# AI Analysis and Deterministic Fallback

## Gemini

The workflow can send the compact analyzed cluster profile to a Gemini chat model.

The AI receives:

- topology summary
- analyzed statistical summaries
- sharding capabilities
- chunk distribution
- current dbStats footprint
- workload distribution
- replication observations
- mongos observations
- resource context
- balancer/migration observations
- data-quality state

The workflow intentionally does **not** send the full raw seven-day time-series dataset or hundreds of discovered metric names to Gemini.

This keeps the AI payload smaller and easier to control.

---

## Deterministic Fallback

Gemini is optional for successful report generation.

If the AI:

- reaches quota
- returns an error
- times out
- produces no usable text
- returns an unexpected output shape

the **Finalize Report** node automatically uses a deterministic technical report generated from the analyzed metrics.

The report then begins with a notice similar to:

```text
Generation mode: Deterministic fallback
```

The metric analysis remains available even when AI interpretation is unavailable.

---

# AI Interpretation Guardrails

The Gemini prompt includes explicit restrictions intended to prevent common monitoring-analysis errors.

Examples include:

- do not infer causality from temporal alignment
- do not call replica sets healthy solely because one PRIMARY and two SECONDARY members are present
- do not infer a bad shard key from workload concentration
- do not infer client load-balancing behavior from similar mongos counters
- do not infer scatter/gather targeting without targeting-specific metrics
- do not call a numeric lag good or bad without a baseline/SLA
- do not assign a cause to replication lag from lag alone
- do not call an oplog window sufficient or insufficient for recovery without recovery requirements
- do not infer WiredTiger cache pressure from cache occupancy alone
- do not recommend RAM/cache resizing from utilization percentages alone
- do not infer balancer failure from zero modeled moveChunk deltas
- do not invent MongoDB shell commands or helpers
- preserve node, service, shard, and replica-set identifiers exactly

The deterministic fallback follows the same conservative interpretation model.

---

# Report Structure

The generated report is organized into the following sections:

```text
# PMM MongoDB Sharded Cluster 7-Day Operational Review

## Cluster Overview
## Data Quality
## Technical Assessment

## 1. PMM Sharding Capability Matrix
## 2. Topology: mongos and Replica Sets
## 3. Data Shard and Chunk Distribution
## 4. Shard PRIMARY Workload Distribution
## 5. Replica Set State and Replication
## 6. mongos Workload, Connections, and Network
## 7. WiredTiger and Member Resource Context
## 8. Balancer and Sharding-Specific Metrics
## 9. Risks and Analytical Limitations
## 10. Recommended DBA Follow-Up
```

The report is marked:

```text
DRAFT - DBA REVIEW REQUIRED
```

before conversion to a text file.

This workflow is intended to assist DBA review, not replace it.

---

# Requirements

## n8n

The workflow was designed around a self-hosted n8n 2.8.x environment.

The following node families are used:

- Form Trigger / Form
- Code
- HTTP Request
- LangChain AI Agent
- Google Gemini Chat Model
- Convert to File

## PMM

PMM must:

- be reachable from the n8n host
- expose the Grafana datasource query API
- contain monitored MongoDB services
- expose a Prometheus-compatible datasource
- expose enough MongoDB metrics for the desired analysis

For full sharding functionality, the PMM MongoDB exporter should expose the shards collector metric:

```promql
mongodb_shards_collection_chunks_count
```

Other capabilities depend on the collectors and metrics available in the specific environment.

## Authentication

The HTTP Request nodes use an n8n **HTTP Basic Auth credential** for PMM.

After importing the workflow, confirm the PMM credential on:

- `Query MongoDB Cluster Catalog`
- `Query MongoDB Metric Inventory`
- `Query Node Metric Inventory`
- `Query Batch PMM Metrics`

Do not place PMM passwords directly inside Code nodes.

## Gemini

Gemini is optional.

If AI narrative generation is desired, configure the Google Gemini credential on:

```text
Google Gemini Chat Model
```

If Gemini is not available, the deterministic fallback still produces the technical report.

---

# Installation

1. Import the workflow JSON into n8n.
2. Open **PMM Connection Config**.
3. Verify or change:
   - PMM URL
   - datasource UID
   - datasource ID
   - timezone
   - lookback period
   - sampling interval
   - max data points
4. Assign the correct PMM HTTP Basic Auth credential to all PMM HTTP Request nodes.
5. Optionally assign a Gemini credential.
6. Save the workflow.
7. Run the form trigger.

---

# Running the Analyzer

## Step 1 — Start the Workflow

Open the form generated by:

```text
Start MongoDB Sharded Cluster Analyzer
```

The workflow performs lightweight MongoDB service discovery.

## Step 2 — Choose a mongos Router

The selection form lists discovered mongos services.

Select a mongos belonging to the cluster you want to analyze.

## Step 3 — Cluster Assembly

The workflow:

1. queries shard membership from the selected mongos
2. identifies shard replica-set names
3. maps those names back to monitored PMM services
4. includes additional mongos routers that expose the same shard set
5. includes config-server services only if explicitly identified

## Step 4 — Metric Discovery

The workflow discovers available MongoDB and node metrics for the assembled services.

## Step 5 — Batch Analysis

Queries are divided into independent replica-set, mongos, and cluster-metadata batches.

Each batch produces compact statistical summaries.

## Step 6 — Cluster Aggregation

The batch summaries are combined into a single cluster-analysis object.

Raw seven-day samples are not merged at cluster level.

## Step 7 — Data Quality

The workflow validates sampling, errors, topology preservation, and batch completion.

## Step 8 — Report Generation

Gemini is attempted when configured.

If no usable AI report is returned, the deterministic fallback is used automatically.

## Step 9 — DBA Review

The final item is marked:

```text
review_required = true
customer_facing_status = DRAFT - DBA REVIEW REQUIRED
```

## Step 10 — File Output

The `Convert Report to File` node converts the `report` field into a text file.

---

# Important Interpretation Rules

## Workload Share Is Directional Context

Shard PRIMARY read/write-rate shares are useful for comparing observed distribution.

They are not asserted to be exact application traffic shares.

## Chunk Count Is Not Data Size

A shard with more chunks does not necessarily hold the same percentage of bytes.

Use chunk count and `dbStats` as separate signals.

## dbStats Is Not Sharded-Collection Balance

The raw per-shard `dbStats` footprint is database-level context.

It should not be treated as an exact measurement of only sharded collection placement.

## Similar mongos Traffic Does Not Prove Client Load Balancing

Similar operation/network averages across routers mean only that the **observed mongos activity is similar**.

They do not prove how clients are configured.

## Replication Lag Has No Universal Threshold

The workflow reports lag statistically and leaves operational severity to SLA, baseline, topology, and application requirements.

## Resource Utilization Does Not Equal Saturation

CPU, memory, filesystem, disk busy, and WiredTiger values are context.

They require trend, workload, latency, queueing, and/or other evidence before recommending resource changes.

---

# Known Limitations

The analyzer is intentionally limited to evidence available through PMM metrics.

It does not currently provide direct evidence for all of the following:

- shard-key quality
- query targeting efficiency
- scatter/gather frequency
- read preference
- zone/range intent
- exact application traffic distribution
- collection-specific query root cause
- current jumbo-chunk state unless a dedicated metric exists
- config-server health when PMM does not explicitly identify config-server services
- exact cause of replication lag
- exact cause of resource asymmetry

For deeper diagnosis, follow-up may require:

- PMM QAN
- MongoDB profiler
- `explain()`
- shard-key definitions
- chunk/range metadata
- zones
- balancer configuration/logs
- collection balancing settings
- primary-shard placement
- MongoDB configuration context
- application behavior

---

# Troubleshooting

## No mongos Router Is Discovered

Possible causes:

- mongos is not monitored by PMM
- exporter role labels are not present
- PMM has no current `mongodb_up` series for the mongos services

Verify PMM inventory and exporter configuration.

The workflow intentionally does not fall back to guessing a sharded cluster from hostnames.

---

## No Shard Membership Is Available

The workflow depends on:

```promql
mongodb_shards_collection_chunks_count
```

for evidence-based shard classification.

If this metric is unavailable, verify whether the MongoDB exporter's shards collector is enabled.

The workflow will not guess shard membership.

---

## Config Server Is Missing

The workflow includes config-server members only when their role is explicitly identifiable from PMM/exporter evidence.

If the report says config-server topology was not detected:

1. verify the config-server members are registered in PMM
2. verify MongoDB exporter metrics are available for those services
3. inspect exporter role labels
4. confirm the PMM MongoDB monitoring configuration

Do not simply classify an unrecognized replica set as the config server.

---

## Task Runner Becomes Unresponsive

The workflow already uses a batched execution architecture specifically to avoid processing the full cluster in one Code node.

Before increasing:

```text
N8N_RUNNERS_HEARTBEAT_INTERVAL
```

check:

- which batch failed
- number of selected metrics
- unexpectedly high-cardinality labels
- query response size
- `max_data_points`
- whether a new raw metric query was added

Avoid reintroducing broad seven-day raw metric discovery into the cluster batch.

---

## Data Quality Shows Missing Samples

The workflow accounts for the `maxDataPoints` query limit.

If a series is still marked poor or warning after that adjustment, inspect the reported missing intervals and exact metric family.

Do not automatically attribute missing data to node load or exporter instability without supporting evidence.

---

## Query/Extraction Error

The Data Quality section includes the affected:

```text
batch
metric key / ref ID
error
```

A small metric-family error results in:

```text
usable_with_caution
```

when the remaining analysis is still usable.

Fix the specific query rather than discarding the entire report.

---

## Gemini Fails

Gemini failure does not prevent report generation.

The **Finalize Report** node supports multiple n8n/LangChain/Gemini output shapes and automatically switches to deterministic fallback when necessary.

Check the final fields:

```text
generation_source
ai_output_shape_detected
ai_error
fallback_reason
```

---

# Security Notes

- Keep PMM credentials in n8n Credentials.
- Do not hardcode passwords in Code nodes.
- The current HTTP nodes allow unauthorized TLS certificates because the example PMM endpoint uses a local HTTPS endpoint.
- For production deployments, prefer a trusted certificate and disable insecure TLS handling when possible.
- Review the generated report before sharing it externally.
- Gemini receives summarized monitoring information when AI generation is enabled. Confirm that this is acceptable under the applicable customer/security policy.

---

# Extending the Workflow

The batched design is intended to make future expansion easier.

Possible future additions include:

- explicit config-server topology discovery
- collection-level sharding analysis
- targeted collStats analysis
- targeted indexStats analysis
- query-targeting/scatter-gather metrics when available
- historical chunk-distribution changes
- validated balancer changelog analysis
- cross-batch event correlation using precomputed compact event summaries
- PMM links for follow-up investigation
- ServiceNow or ticket integration
- scheduled weekly/monthly execution
- customer-facing report formatting after DBA approval

When adding metrics, prefer:

```text
small modeled query
    ->
batch-level analysis
    ->
compact semantic summary
```

instead of forwarding raw high-cardinality time series into the cluster aggregator or AI model.

---

# Design Principles

The workflow follows these core rules:

1. **Discover before assuming.**
2. **Use MongoDB topology evidence instead of naming conventions.**
3. **Analyze each operational layer according to its role.**
4. **Keep raw time-series processing inside bounded batches.**
5. **Merge summaries, not raw data.**
6. **Treat PMM metrics as evidence, not automatic diagnosis.**
7. **Never convert correlation into causality without evidence.**
8. **Do not apply universal performance thresholds where none are defined.**
9. **Make missing capabilities explicit.**
10. **Require DBA review before customer-facing use.**

---

# Summary

The MongoDB Sharded Cluster PMM Operational Analyzer provides an automated, evidence-driven way to turn PMM metrics into a structured 7-day MongoDB sharded-cluster review.

Its main value is not simply collecting metrics. It organizes them according to MongoDB topology and converts them into DBA-reviewable observations about:

- cluster composition
- workload concentration
- shard distribution
- replication behavior
- resource differences
- balancer/migration context
- monitoring gaps
- next-step investigation

while deliberately limiting unsupported conclusions.

The workflow should be treated as a **technical analysis assistant for DBAs**, with deterministic reporting available even when AI narrative generation is unavailable.
