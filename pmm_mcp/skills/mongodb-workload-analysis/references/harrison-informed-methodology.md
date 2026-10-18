# Top-down MongoDB workload assessment — Harrison-informed lens

## Scope and attribution
This is an **independently authored DBA workflow**, informed by the publisher's public description and contents of *MongoDB Performance Tuning: Optimizing MongoDB Databases and Their Applications* (Guy Harrison and Michael Harrison, Apress, 2021). The publisher describes a repeatable top-down process that addresses common fundamental performance problems first, then code, server and cluster tuning. This document is **not** the book's text, a detailed reproduction, or a claim that either author designed or endorsed this Skill. Current MongoDB and Percona references remain the source of truth for version-specific technical details.

Public source: https://link.springer.com/book/10.1007/978-1-4842-6879-7
Official MongoDB performance reference: https://www.mongodb.com/docs/manual/administration/performance-tuning/
Official MongoDB query diagnostics: https://www.mongodb.com/docs/manual/tutorial/evaluate-operation-performance/

## Principle 1 — Start with the work, not the machine
Before analyzing CPU, RAM or disks in isolation, establish **what MongoDB was asked to do** over the reporting period:
- Cluster/node role, monitored scope, time bounds and reliable data coverage.
- Query/insert/update/delete mix; peak and sustained workload periods; business or application baselines when supplied.
- Whether there is measured latency regression, queueing, service risk or simply heavier successful throughput.
- If query-level data exist, which query families/commands most strongly contribute to the *observed* load.

A large CPU graph with no workload and latency context is not proof of inefficient MongoDB behavior. Increased resource use can represent useful additional work.

## Principle 2 — Look for disproportionate work and high-leverage opportunities
When PMM QAN and sampling permit, investigate the highest-impact **query shapes**, not merely the slowest one-off samples:
1. Establish observation period, population, sampling, and comparability of fingerprints.
2. Rank by **observed frequency**, **individual latency**, and **reported aggregate time/load** where these are truly available and meaningful.
3. Correlate high contributors with docs/keys examined, results returned, COLLSCAN/IXSCAN or plan indicators *if recorded*.
4. Differentiate a **frequent moderately expensive query** from a **rare expensive query**; either may matter depending on customer objectives and measured impact.
5. Check evidence of unnecessary repeated reads, large returned payloads, expensive sorts/aggregations, write amplification, or poor selectivity only if collected data support those mechanisms.

**Statistical caution:** count × mean latency is not necessarily CPU time or exclusive server load; concurrent operations overlap. Sampling rates and QAN coverage may prevent extrapolation to the full workload. Do not rank all workload cost from incomplete samples as though they were census data.

## Principle 3 — Prefer foundational efficiency checks before costly tuning
Use the evidence-driven sequence below, adapting to problem and customer safety:

| Layer | Investigate (only with evidence) | Before recommending a change |
|---|---|---|
| Application and query behavior | Repeated operations, poor query shape, response sizes, aggregation stages, query predicates | Identify fingerprint/operation and whether app behavior can change safely |
| Schema and indexes | Documents/keys examined vs returned, use of sort/covering indexes, document growth, index selection | Validate with plan and realistic workload, index build space/write overhead, regression tests |
| Writes and transactions | Update rate, multi-document writes, transaction duration/contended activity | Distinguish command from modified-document metrics; check isolation/business behavior |
| MongoDB engine and OS | Cache read pressure, eviction, checkpoints, memory, queues, CPU/disk latency | Correlate metrics; respect storage engine and version-specific semantics |
| Topology, replication, scaling | Secondary resource headroom, lag, oplog window, per-shard skew, growth | Validate workload distribution, operational risk, HA and change controls |

This is an **analysis ordering**, not permission to ignore a critical operational incident while exploring query improvements. If PMM contains no query evidence, stay at engine/infrastructure assessment and propose the appropriate follow-up.

## Principle 4 — Diagnose with alternative hypotheses and counter-evidence
Every substantive finding must include:
- What was observed: metric, value, unit, target/role, UTC window and collection coverage.
- What *other* independent metric corroborates or contradicts the diagnosis.
- A plausible alternative explanation and any test completed to exclude it.
- What would falsify the current interpretation; if unavailable, classify as hypothesis, not a proven cause.

Example hypothesis ladder (hypothetical, not customer data):
- Observation: update operations and CPU rise together on a primary.
- Check: throughput/latency, QAN fingerprints, disk write latency, WT dirty cache and checkpoints.
- Alternative: an expected batch workload instead of inefficient query execution.
- Conclusion: describe temporal association unless query-level or workload evidence supports a narrower mechanism.

## Principle 5 — Recommendations must show value and verification
For each suggested action, write:
- **Target and priority:** affected fingerprint, node or metric-defined risk; customer-defined SLO if available.
- **Why now:** evidence that the issue is sustained, recurrent, costly or operationally important.
- **Candidate intervention:** preferably read-only investigation first, then a controlled test.
- **Trade-offs:** new indexes can increase write costs and storage; cache/configuration tuning can shift pressure; scaling is not a substitute for efficient queries.
- **Success measure:** named comparable metric(s), baseline period, expected direction, post-change validation window and rollback/approval route.
- **Evidence grade:** Confirmed / Supported hypothesis / Unverified hypothesis / Insufficient data.

Do not provide invented numerical savings or guaranteed improvements. A PMM retrospective is not a full root cause investigation without corroborating data.

## Deliverable acceptance test
An experienced MongoDB DBA should be able to answer:
1. Which activities make up the workload and when do they become expensive?
2. Is added resource use explained by more useful work, less-efficient work, or is that unresolved?
3. Are any **specific** application/query/schema inefficiencies supported by QAN or explain data?
4. Is there sustained engine, storage, concurrency, replication or capacity risk?
5. What is the lowest-risk next step with the highest evidence-supported potential value?
6. What counter-evidence or missing monitoring would change this assessment?

If query evidence is absent, explicitly say questions 2–3 cannot be answered fully. Do not fill gaps with textbook patterns.
