# Analysis and validation rules

- Match PMM data to the report scope by the exact inventoried service names.
- Report each member's role in cluster identity and topology. State `unknown` when telemetry is absent, conflicting, or unrecognized.
- MongoDB state code `1` means primary and `2` means secondary. Other known codes are states, not primary or secondary roles; do not infer a role from a node name.
- Evaluate the exactly-one-primary invariant only when every scoped member has a known, non-conflicting state and all latest observations are within 60 seconds of one another.
- A missing member state, more than one primary, zero primaries, or samples outside the alignment window must remain visible as a warning or as not checked.
- Treat member state as a point-in-time Prometheus observation. It does not show election history or establish service health.
- Do not interpolate missing range samples or interpret unavailable metrics as zero. Do not make causal claims from correlation alone.
- Treat configured thresholds as observational review triggers. Correlate them with workload, latency, service objectives, and customer impact before recommending a change.
- The report validator's `invalid` status indicates a structural problem. Its `warning` status preserves data or consistency gaps that need to appear in the deliverable.
