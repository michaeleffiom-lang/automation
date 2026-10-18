# PMM-to-MongoDB metric correlation catalog

Use this catalog as a **conceptual mapping**, not a list of guaranteed Prometheus identifiers. Dashboard panel names and exporter time series differ by PMM/exporter release. Discover live series first; record exact series names, labels, units and dashboard URLs in Evidence Register. Do not invent labels, queries, or sources.

| Domain | Gather if available | DBA interpretation / independent cross-checks | Avoid |
|---|---|---|---|
| Topology, uptime, visibility | member role, uptime, monitoring status, MongoDB version, topology transitions | Missing host data, restart, election, hidden/delayed/arbiter nodes can distort time plots | Silently aggregating changing roles or stale targets |
| Workload mix | query/insert/update/delete/getMore/command rates; docs inserted/updated/deleted/returned; QPS | Separate operations from documents; compare peak workloads to CPU, I/O, latency | Calling ops/s "documents per second" |
| Latency | MongoDB command/query/read/write latency, QAN p95 if available, observed samples | Latency vs throughput, queueing, disk time, query shape; check which statistic (mean, sum/count, histogram, sampled) | Claiming p95/p99 when only mean/snapshot exists |
| CPU / memory | host CPU core and total usage, mongod CPU, load, available memory, RSS, swap | Throughput-normalized CPU only with stable definitions; CPU saturation requires capacity/queue/latency evidence | Diagnosing bottleneck from CPU spike alone |
| WiredTiger cache | current/max cache bytes, dirty bytes, pages read/written, eviction work/activity, cache read pressure | Occupancy + eviction + page read rate + OS memory + read/write latency | Calling occupancy close to max an automatic problem |
| WiredTiger concurrency | active readers/writers, queued readers/writers, ticket concurrency & availability (version-dependent) | Long-lived queue and increasing latency vs active work; 7.0+ dynamic tickets | "0 available tickets proves overload" in 7.0+ |
| Checkpoint / journal | checkpoint duration, frequency, log activity and dirty cache (as exposed) | Compare checkpoint duration to write throughput, storage latency and queueing | Treating every long checkpoint as failure |
| Disk I/O | read/write bytes/s and ops/s, device latency, utilization, await, free capacity | Compare I/O mix to WT reads/writes, operation load, checkpoint activity | Assuming high IOPS means saturation without device characteristics |
| Query efficiency | QAN query fingerprints, count, latency, scanned docs/keys, returned docs, plan summary | Examine scan/return ratio when denominators nonzero; high exam count may be legitimate for aggregations | Recommending an index without query plan, predicate, selectivity, and write-cost evaluation |
| Replication | per-member lag, last applied/committed times, oplog window, application buffer, flow control, elections | Compare primary write pressure and each secondary's I/O and replication processing; relate window to backup/recovery needs | Interpreting secondary metrics as primary write path or equating lag with loss |
| Connections | current/active/available (version dependent), network traffic, concurrent ops, queues | Connection growth with active requests and server latency; distinguish monitoring/hello/idle from actual workload | Saying connections alone cause high CPU |
| Growth/capacity | volume bytes used/available, logical/physical db growth, oplog consumption | Trend conditioned on retention and workload pattern; flag fragmented metrics only with their semantics | Naive linear extrapolation from two endpoints |
| Sharded cluster | per-shard read/write load, CPU, storage, member roles, query routing and chunk balancing | Compare like-for-like shard roles and time windows, detect asymmetry; evaluate targeted/scatter-gather if query data exists | Calling shard distribution skew a proven cause without evidence |

## Workload contribution and query-tuning decision table

Use this before prescribing server changes, but only when the relevant QAN metrics and representative coverage exist.

| Question | Evidence to retrieve | Interpretation and limits |
|---|---|---|
| What drives workload volume? | Command-type operation rates, node role, durations of peaks | Separate expected growth in useful work from measured deterioration |
| Which query shapes create repeatable cost? | Fingerprint frequency, latency distributions where sampled, aggregate load if PMM explicitly exposes it | Sampling bias can prevent true whole-workload rankings; elapsed time does not equal CPU time |
| Is data access efficient? | Per-shape examined/returned counts, plan summaries, sort indicators if available | Requires actual query evidence; a large scan can be intentional for an analytic pipeline |
| Could an index or schema change help? | Predicates/sorts, relevant plan, selectivity, known index list, write mix | Assess index maintenance/storage and regression risks before proposing changes |
| Should storage or capacity change? | Sustained request latency/queues, WT read/eviction, device latency, validated growth | Resource increase alone is not justification; prioritize documented operational safety risks |

## Mandatory correlation recipes

### Recipe A: CPU rise
1. Locate sustained high CPU windows with duration and node context.
2. Overlay operations mix and QPS; if proportional growth, characterize volume-driven load, not inefficiency.
3. Cross-check latency and queues. Inspect QAN fingerprints if available; see whether expensive scans/aggregations rose.
4. Check WT read/eviction behavior and disk latency. Discuss alternate causes (background work, checkpoint, compaction, missing QAN).
5. Conclude `confirmed/supported/unverified/insufficient`, not root cause from coincidence.

### Recipe B: Memory/cache concern
1. Distinguish host memory, process RSS, WT internal cache, filesystem cache, container limits.
2. Compare cache usage/max with dirty fraction, eviction activity, page reads and read latency, swap and OS available memory.
3. Assess *sustained* pressure and impact. Cache usage near limit alone is expected and inconclusive.
4. Suggest review of read access patterns and working-set efficiency before changing cache size.

### Recipe C: Read/write latency
1. Identify affected operation class and time windows.
2. Overlay QPS/operation mix and active/queued requests.
3. Inspect QAN query shape if available, cache read/eviction, device latency and IOPS.
4. Identify whether evidence favors query, contention, or storage hypothesis; qualify unknowns.

### Recipe D: Replication and recovery
1. Compare each secondary lag with primary write/oplog generation during the same windows.
2. Check secondary CPU, storage latency, apply metrics/buffer as available, flow control (primary only), member state changes.
3. Review effective oplog window against observed lag **and known backup/maintenance duration**, if provided.
4. Recommend investigating sustained lag or reduced recovery headroom; do not size the oplog from guesses.

### Recipe E: Capacity
1. Establish units, data completeness, whether growth is persistent vs cyclic, and filesystem/device identity.
2. Compare disk bytes used, storage consumption and write/oplog workload.
3. A forecast requires sufficient coverage, model fit, stable regime and explicit assumptions. Include uncertainty and no extrapolation when assumptions fail.

## Summarizing the requested review window
- Identify baseline, sustained peaks and peak **duration**, periods by day-of-week/hour when reliable, and meaningful abnormal windows.
- Summaries: min/max/mean if valid; percentiles only from appropriate raw distributions; sample interval/resolution; never compute percentile of plotted averages and present as request latency percentile.
- Rates from cumulative counters require reset-aware derivation; sum of rate series only for compatible label sets (avoid double counting router/primary/secondaries).
- Compare top findings not isolated maxima. With uneven series resolution and missing points, document uncertainty.
- If comparing windows, use matched duration, same service role, same units, same aggregation and coverage. Handle zero denominator and major topology shifts explicitly.
