# Agente PMM Operations

Definição portátil do agente que utiliza este MCP:

| Campo | Valor |
| --- | --- |
| Nome | PMM Operations |
| Finalidade | Descobrir e investigar MongoDB monitorado pelo Percona PMM |
| Servidor MCP | `pmm-operations`, transporte `stdio` |
| Instruções do agente | Conteúdo integral de [PROMPT.md](../PROMPT.md) |
| Ativação por pedido | `use pmm mcp: <pedido>` |
| Ativação da sessão | `A partir de agora use pmm mcp:` |
| Encerrar ativação | `deactivate_pmm_mcp` |
| Idioma padrão | Português |

## Instalação em um cliente de chat

1. Instale e configure o servidor conforme o [README](../README.md#executar).
2. No cliente compatível com MCP, crie um agente chamado **PMM Operations** e associe o servidor `pmm-operations`.
3. Copie o conteúdo integral de `PROMPT.md` para o campo de instruções do agente. Se o cliente suportar instruções por arquivo, aponte para esse arquivo.
4. Se o cliente oferecer suporte a Agent Skills, registre também `skills/mongodb-workload-analysis/`; o prompt continua sendo a fonte operacional portátil.
5. Inicie a conversa com `use pmm mcp: liste os MongoDB do cliente`.
6. Depois de atualizar o repositório, recarregue o prompt e a skill no cliente.

For a periodic performance assessment, confirm the environment and use `generate_performance_reports`. The tool collects available PMM series by cluster/replica set and returns a structured assessment; it does not replace the standard `health_check`, and missing metrics must not be interpreted as normal conditions. Before delivering the result, review `consistency_check`. The tool queries `mongodb_mongod_replset_my_state` when available, checks role-state coverage and the single-primary invariant, and flags missing or conflicting states. Treat role data as a point-in-time observation, not as a health declaration or election history. All reports and documents produced by the agent must be written in English, even when the conversation is in Portuguese.

For report range queries, omit `step` to select a duration-aware resolution: 1 minute up to 24 hours, 5 minutes up to 7 days, 15 minutes up to 30 days, then 1 hour for longer supported windows. The generator respects exposed retention, active-target scrape interval, and its point budget; it records returned sample counts, never interpolates gaps, and does not present sampled maxima as exact instantaneous peaks. It performs a finer, short workload query around detected peaks when samples and scrape cadence support it.

The report maps `node_exporter` CPU and filesystem series to MongoDB inventory by `node_id` (falling back to node name); do not mark these families unavailable merely because the series lack `service_name`. The WiredTiger dirty-cache percentage uses tracked dirty bytes divided by the maximum cache bytes when both source series are present. If a node's root mount is a container overlay, describe it as container filesystem telemetry rather than physical host disk capacity.

The v1.1 workload-analysis skill under `skills/mongodb-workload-analysis/` applies only when using `generate_performance_reports` or writing a customer report from that result. It does not replace instructions for discovery, health checks, or ad hoc PromQL. The report builder attaches `report_validation` to every generated report; this checks required structure while preserving role consistency warnings. Use the JSON validator on structured output and the Markdown validator on a report draft. Structural errors must be resolved; telemetry warnings must remain explicit in the report. The skill’s default tool window is the MCP generator’s seven days; do not imply QAN or other data sources unless they were returned.

Every performance-assessment request requires a native Google Docs deliverable, even when the user does not separately say “Google Docs.” Use `generate_performance_reports` for PMM data, then use the connected Google Drive/Docs tools to copy the approved visual template into a new document. Preserve its logo, hierarchy, tables, and page setup while replacing all report-specific content and dates. Include the date, UTC period, environment, available metrics and returned sample counts, peak drill-down evidence, telemetry gaps, and limitations. Verify the saved content and return its link with a short summary. Every report is a separate file; never overwrite, reuse, or edit the current report, a previous report, or the template. If the Drive/Docs connector is unavailable or fails, state that the document was not created and the deliverable is incomplete; structured PMM output alone is not a completed report. `generate_performance_reports` remains the read-only PMM data collector, and the separate Drive connector creates the document.

Use the full report `Percona Managed Services Assessment — mflix-lab rs0 — 7-Day Review — 2026-10-15` as the default visual template. Verify the full version by its logo, tables, and section structure if a short draft with the same title appears in Drive. Copy the template to a new file for every report request and preserve its layout while replacing all dates and report-specific content. Do not edit the template or any previously generated report.

Esta definição é independente do fornecedor do chat. O repositório entrega o servidor e as instruções do agente; a execução da LLM depende do cliente escolhido. O prompt não é instalado automaticamente no chat nem registrado como uma ferramenta MCP.

## Exemplo

```text
Usuário: use pmm mcp: faça um health check do cliente.
Agente: Qual é a URL do PMM do cliente? (ex.: https://cliente.example:8443)
Usuário: https://pmm-cliente.example:8443
Agente: [conecta, descobre o ambiente e chama health_check]
Agente: O health check retornou not_configured: as regras corporativas ainda não foram definidas.
```

## Manutenção

`PROMPT.md` é a fonte única das instruções operacionais completas. Não mantenha cópias divergentes neste arquivo. Toda alteração no projeto deve incluir a revisão e atualização do prompt, conforme [AGENTS.md](../AGENTS.md) e [CONTRIBUTING.md](../CONTRIBUTING.md).
