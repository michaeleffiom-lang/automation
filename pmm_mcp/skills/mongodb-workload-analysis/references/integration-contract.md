# Adapting this Skill to your existing PMM MCP and report generator

## Contract for this repository

For this PMM MCP, this skill is applied to the `generate_performance_reports` tool and customer reports derived from its result. The tool takes `client_id`, `period_hours`, `end`, `step`, `cluster`, and `replication_set`; its default review window is seven days. Use `list_pmm_clients` to identify configured targets and confirm the selected cluster and replica set from returned inventory/report scope.

The collection response contains a top-level `generated_at`, scope and a `reports` array. A report may include a period, sample step, member topology, executive assessment, metric summaries by member, actual `metric_sources`, unavailable metric families, limitations, and role consistency data. Use only fields present in the live result. The current generator does not promise QAN fingerprints, query plans, request-latency percentiles, memory, disk I/O, alert history, MongoDB version, PMM version, or dashboard links. Mark these as unavailable unless another report-scoped source actually returns them. Never use the illustrative sample values below as customer evidence.

The report builder in this repository adds `report_validation` for structural validation and `consistency_check` for replica-set role coverage and exactly-one-primary review. If a connected MCP deployment returns an older schema without these fields, validate the actual report structure with `scripts/validate-report.py`; if member roles are missing, query `mongodb_mongod_replset_my_state` using the exact inventoried service names when `query_prometheus` is available. Record the timestamp and describe this as a supplemental point-in-time check. Do not silently present an old schema as though the new checks ran.

For evidence provenance, assign report-local IDs (`E-001`, etc.) and identify the returned metric source, service, role, UTC period or sample timestamp, statistic, unit, step, and observed sample count. Use a real query or panel link only if the MCP returns it. Do not invent source links or query text. A range maximum is a sampled maximum and does not establish how long the value persisted.

The structured report validator is `scripts/validate-report.py`; it checks the MCP JSON structure. The supplied v1.1 Markdown validator is `scripts/validate_report.py`; it checks a customer-facing draft and evidence-ID references. Both are structural checks only and do not prove analytical correctness. For a Google Docs deliverable, validate the Markdown draft, then transfer its verified content into a new copy of the approved native Google Docs visual template as described in `PROMPT.md`.

The rest of this reference documents the general integration pattern and example evidence shape; it is illustrative, not an additional tool or a promise about the live MCP schema.

The Skill is **agent-side guidance**; it is not an MCP server or an API endpoint. Copy the folder to a location recognized by the host (for example a repo `skills/` folder when the host supports it). Exposing the folder in a repository does not automatically register it. Your agent must discover/load `SKILL.md` on relevant requests, and the same agent must have access to your running PMM MCP server tools.

## Suggested agent orchestration
1. User requests a MongoDB performance report for the requested cluster and period.
2. Agent loads this Skill and resolves scope.
3. Agent selects **existing** MCP tools that provide time-series query, PMM inventory, panel metadata and, if enabled, QAN; these tool names are deployment-specific. If a capability is absent, record the gap rather than fabricate output.
4. Adapter normalizes raw results to the evidence format below, preserving source URLs or query text and labels.
5. Agent follows investigation playbook and generates customer Markdown using supplied template.
6. Invoke validator on generated Markdown. Deliver report plus transparency about gaps.

## Suggested normalized evidence payload (illustrative contract, not built-in MCP schema)
```json
{
  "report": {
    "customer": "Example (replace)",
    "cluster": "example-rs",
    "start_utc": "2026-09-01T00:00:00Z",
    "end_utc": "2026-10-01T00:00:00Z",
    "mongodb_version": "unknown",
    "pmm_version": "unknown",
    "deployment_type": "replica_set"
  },
  "evidence": [
    {
      "id": "E-001",
      "metric": "mongodb.operation_rate",
      "actual_series": "<actual returned PMM identifier>",
      "source": "<real dashboard URL or actual query>",
      "node": "example-primary",
      "role": "primary",
      "window_utc": ["2026-09-21T14:00:00Z", "2026-09-21T15:00:00Z"],
      "unit": "ops/s",
      "aggregation": "reset-aware 5m rate",
      "resolution": "60s",
      "coverage": "complete",
      "observations": "<values from actual query>"
    }
  ]
}
```
The example values above **must never be copied into a customer report**. Any internal metric alias such as `mongodb.operation_rate` is conceptual; use the actual exporter series for provenance.

## Host responsibilities
- MCP authenticates and enforces allowed customer/cluster scope.
- MCP delivers real time series with units, timestamps and labels; the report generator preserves provenance and missing-data flags.
- Host injects tool descriptions and actual dataset; Skill loads supplementary references as needed.
- Optionally pre-aggregate safe statistics in deterministic code; keep natural language conclusions evidence-based.
- Configure graph links using real returned dashboards/panels; omit if unknown.
- Do **not** mount sensitive credentials into the Skill itself or save customer metrics in bundled files.

## Integration options
- **Agent-driven generator:** load Skill in the agent that calls MCP and authors the report.
- **Code-driven generator:** run existing script to retrieve and structure PMM results, then pass its evidence object and this Skill's instructions to a language model; use `customer-report-template.md` for Markdown output. Agent Skill discovery is not automatic in a non-agent script, so explicitly load/inject instructions there.

## Acceptance tests
1. Given cache occupancy near maximum but no eviction/latency data, result must **not** claim proven memory pressure.
2. Given MongoDB >=7.0 and 0 available tickets, result must **not** diagnose overload without sustained queue/latency evidence.
3. Given no QAN data, result must **not** recommend a specific index or claim queries are efficient.
4. Given higher write rate and replication lag, result must distinguish association from established cause.
5. Given missing 40% of samples, result must qualify coverage and refuse precise capacity forecasting.
6. Given only primary metrics, result must not assert secondary health.
7. Given no customer-impact metrics, result must not assert end-user impact.
8. Two periods with different member roles/resolution must not be compared without caveats.
