# Evidence standards and quality gates

These constraints are REQUIRED for every customer report.

## Provenance and scope
- Customer-specific claims must originate from retrieved PMM time-series, QAN data, read-only supplemental inputs explicitly supplied for this report, or supplied customer context. External technical documentation can only explain how MongoDB works.
- Every numerical observation needs: metric name/semantic identity; exact target/node and role; UTC observed window or timestamp; value and unit; aggregation/sample resolution; source panel/series link or query identifier; coverage/gaps.
- Store those records as evidence IDs (`E-001`, `E-002`). A claim without a traceable evidence ID must be removed or explicitly labeled as an unverified question.
- Do not present diagrams or sampled screenshots as measurements without underlying series.
- Never assume a metric missing from PMM is zero; say `Not available`.
- Don't invent or copy unsupported 'root causes', lock waits, collection names, query shapes, downtime, storage type, customer impact, service-level thresholds or tuning settings.

## Numeric/statistical rules
- Counters are cumulative: use valid reset-aware *rates* over a specified interval to discuss throughput, and investigate restarts/resets.
- Gauges are point-in-time: don't apply a rate if it is not meaningful; avoid comparing dissimilar times or units.
- Percentiles of gauge series are **time-distribution percentiles**, not p95 request latency. p95/p99 request latencies require suitable distributions/histograms/raw observations.
- Avoid mixing pre-aggregated averages without correct weighting. Never compute "average of averages" without compatible weights.
- Don't assume a peak persists across the reporting window: quantify duration or label it as an isolated observation.
- Window-over-window percent change uses the same metrics, units, roles, resolution and comparable coverage; don't compute if baseline is zero without a special representation.
- Avoid forecast if multiple regimes, large gaps, insufficient history, recent reconfiguration or cyclical usage undermine linear fit.

## Correlation vs causation
- One metric rising with another is a *temporal association*, not a demonstrated cause.
- A recommendation to alter indexes, oplog, cache, threads, hardware, sharding or server parameters requires evidence-based hypothesis, expected benefit and risk, testing plan, rollback/approval requirements. Prefer an investigation action when data insufficient.
- Include alternative explanations for substantial findings.
- Report evidence-based absence carefully: `No sustained increase was observed in the available [metric] for [window]` rather than `No bottleneck exists`.

## Version-aware checks
- Determine MongoDB version before interpreting ticket thresholds; 7.0+ dynamic ticket adjustment means zero available tickets alone is not overload.
- PMM and exporter versions may change names, units or available dashboard panels. Resolve at runtime.
- QAN stored metrics reflect observed/sampled operations and collection method, not necessarily all executed queries.
- Flow control metrics meaningful on primary; secondary placeholder values are not diagnostic.
- Node role changes and host replacement break naive comparisons.

## Security and safety
- Use read-only collection. Do not dump PII or unredacted raw query parameters into customer report; mask account IDs, IPs, credentials and query literals unless authorized and necessary.
- Follow customer scope and least privilege. Do not modify profiler, MongoDB settings, indexes, or traffic based solely on this Skill.
- Report potential destructive changes as proposed options requiring customer review and controlled change approval, never as approved actions.

## Final checklist
- [ ] Time bounds, timezone, deployment scope and versions reported; unknowns explicit.
- [ ] Coverage, sampling and missing data recorded.
- [ ] Numeric statements have valid units/window/node/aggregation/evidence IDs.
- [ ] Graph interpretations correlate at least two independent series when causal inference is discussed.
- [ ] Each substantial finding offers a plausible alternative explanation.
- [ ] Version-sensitive MongoDB details used correctly.
- [ ] Query-level assumptions not made when QAN absent.
- [ ] Recommendations have measurable verification criteria and do not pre-authorize changes.
- [ ] Report is transparent that PMM-only review is not a full RCA.
