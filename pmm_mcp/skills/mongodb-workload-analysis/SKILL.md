---
name: mongodb-workload-analysis
description: Apply evidence-led senior DBA analysis when generating a MongoDB performance report with the PMM MCP generate_performance_reports tool.
metadata:
  version: "1.1.0"
  domain: "mongodb"
  applies-to: "generate_performance_reports"
---

# MongoDB Workload Analysis — Report Generator

This skill applies only when the user requests a performance assessment through `generate_performance_reports` or asks for a customer-facing report based on that tool. It does not change discovery, health-check, or general PromQL workflows. The skill is agent-side guidance; it does not add or register MCP tools.

## Report workflow

1. Confirm the PMM client, cluster, replica set, and period. The tool defaults to seven days; use the requested period when supplied. Record exact UTC bounds, step, generated timestamp, and known MongoDB version. Say when version or environment details are unavailable rather than guessing.
2. Read `references/integration-contract.md` for this MCP's response contract, then call `generate_performance_reports` and inspect the complete response. Treat its `reports[]` data, `metric_sources`, per-member sample counts, `consistency_check`, `report_validation`, and `limitations` as the evidence boundary. This generator may not provide QAN, query plans, latency, memory, disk I/O, alerts, or all host metrics. Do not imply those were inspected unless another available report-scoped source actually returned them.
3. Apply the top-down method in `references/harrison-informed-methodology.md`: characterize workload first, then correlate engine, host, replication, and capacity evidence as available. For every significant interpretation, include an alternative explanation and state what the collected data cannot establish. Read `references/evidence-rules.md`, `references/investigation-playbook.md`, and the applicable sections of `references/metric-catalog.md`, `references/metrics.md`, `references/recommendations.md`, and `references/mongodb-reference.md` as needed.
4. Preserve per-member scope and role. Review role coverage and the exactly-one-primary check. If `consistency_check` is absent or incomplete and the state metric is available, query `mongodb_mongod_replset_my_state` for the exact inventoried services, map state codes using `references/analysis-rules.md`, and report the aligned snapshot time. Do not call a snapshot history or health status.
5. Run `scripts/validate-report.py` on the structured JSON report. If preparing a Markdown report draft, use `assets/customer-report-template.md` and run `scripts/validate_report.py` on the draft; resolve structural errors and preserve data gaps as explicit limitations. A passing structural check does not verify technical truth.
6. For customer-facing analysis, separate observations, correlations, hypotheses, operational implications, and next actions. Assign evidence IDs and include the actual metric/source, member and role, UTC window, unit, aggregation/resolution, and coverage in the evidence register. Do not claim causal root cause, customer impact, trend, or threshold breach without corresponding evidence.
7. If the user requests Google Docs, create a separate native document for each report using the repository's approved visual template and Google Drive/Docs workflow in `PROMPT.md`. Preserve the source template and prior reports, use a unique title with cluster and local date/time, and verify the saved document. Use `templates/customer-report.md` as the section map and the v1.1 Markdown asset as the evidence checklist; neither replaces the approved Google Docs layout.
8. Write reports in English. State that the assessment is an investigation, not the standard health check or a declaration of service health. Return the saved document link when applicable.

## Safety

Use read-only PMM and Prometheus collection. Never run `explain`, enable profiling, change indexes or MongoDB settings, or execute mutating operations without explicit authorization. Do not include credentials, `.env`, `clients.json`, `.pmm-cache/`, unredacted query literals, or real customer measurements in this skill package.
