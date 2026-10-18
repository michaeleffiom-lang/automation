# Senior DBA analysis playbook

Use after identifying report scope and evidence coverage. This file is deliberately procedural. Apply the top-down assessment in `harrison-informed-methodology.md` while retaining safety-first treatment of immediate operational risks.

## Stage 1 — Inventory and risk framing
- Identify deployment type: standalone, replica set, sharded cluster; primary and secondary members per time; host/container; MongoDB/PSMDB major and FCV if available.
- Determine which requested-window metrics actually exist. Identify collection gaps, monitor restarts, downsampled windows, read-only QAN availability and whether data includes all nodes.
- Record customer-specific objectives/SLOs if supplied; if not available, avoid invented SLA or thresholds.

## Stage 2 — Rank workload contribution before tuning infrastructure
- Ask whether more operations, more expensive operations, or incomplete data best explain the observed trends.
- When QAN coverage allows, identify high-leverage fingerprints by frequency and per-operation cost; rank **reported aggregate load** only when that concept is present and its sampling semantics are clear.
- Examine query and application behavior first when justified: shape, indexes, aggregation, repeated calls and write patterns. If QAN is absent, mark query-level efficiency unassessed; do not infer missing indexes from host metrics.
- Do not interpret query elapsed-time totals as CPU time, extrapolate sampled values without a defensible estimator, or hide write and storage trade-offs of tuning.

## Stage 3 — Map workload and anomalies
- Describe read/write ratio, operation mix, throughput peaks, service role, and sustained windows, not just maximum points.
- Inspect latency by operation class and any QAN fingerprints. Distinguish workload growth from degraded efficiency.
- Relate resource changes to volume and *shape* (scans, updates, aggregation), not just a graph overlay.

## Stage 4 — Engine and infrastructure correlation
- WiredTiger cache: occupancy -> eviction/page read/dirty -> filesystem memory -> storage I/O -> latency.
- Concurrency: active operations -> queues -> ticket behavior for known version -> latency. MongoDB 7+ ticket "available=0" is not diagnostic by itself.
- I/O: write activity -> dirty cache/journal/checkpoint -> disk latency -> user latency, if measurable.
- Distinguish spikes caused by collection, backup, retention or monitoring from actual regression only when supporting data exists.

## Stage 5 — Query investigation
- Use historical QAN **only when enabled** and check sampling/profiling collection methods and coverage.
- Rank fingerprints by total load and tail latency when supported. Check frequency x per-query cost, docs examined / returned, keys examined, plan summary and command type.
- Query recommendation must specify: evidence, query-shape investigation, possible plan validation, impact on writes, risk of index proliferation. Never guarantee speedup without tests.
- Missing QAN means "query-level contributors unverified", not "queries healthy".

## Stage 6 — Replication, recovery, HA
- Validate member roles and any elections/restarts during reporting period; summarize availability only from reliable member-state metrics.
- Compare primary generation rates with secondary apply rate, lag, storage, recovery headroom, flow control.
- For sharded clusters consider imbalance across shards and mongos routing if exposed; don't use replica-set aggregate values to describe shard-level distribution.

## Stage 7 — Synthesize findings and counterexamples
For each finding use:
```
Finding ID: F-001
Observation: <value, unit, node, UTC start/end, observed metric>
Evidence: E-001, E-002
Correlation: <independent metric and overlap in time>
Interpretation: <specific MongoDB mechanism with documentation reference>
Alternative explanation: <plausible competing explanation and whether evidence rules it out>
Status: Confirmed | Supported hypothesis | Unverified hypothesis | Insufficient data
Confidence: High | Medium | Low
Customer impact: Observed | Possible | Not assessed
Action: <specific read-only check / controlled proposal>
Validation: <what evidence would confirm or invalidate>
```
- "Confirmed" reserved for directly demonstrated diagnosis, such as documented query plan showing a COLLSCAN over high scanned documents tied to observed query cost; mere simultaneous CPU and query increases are not causal proof.
- "High priority" requires impact, sustained risk, clear operational safety or customer SLO grounding. Don't sensationalize a warning threshold alone.
- Recommend data capture for unresolved issues over speculative tuning.
- Evaluate recommendation leverage in order: evidence-supported query/application/schema efficiency, engine and storage, replication/topology, capacity; justify when a safety issue requires jumping ahead.
- For each proposal state likely trade-offs and a measurable pre/post validation plan, without claiming guaranteed speedups.

## Stage 8 — Deliver
- Populate `assets/customer-report-template.md`; register evidence with identifiable PMM source and timestamp; link MongoDB docs to interpretation, not invented findings.
- Order results by materiality; include what appears stable **only when observed**; call out missing data limitations.
- Report coverage and sampling, versions known/unknown, and distinction between the requested-window workload review and a root cause analysis.
