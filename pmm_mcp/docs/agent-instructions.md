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

The v1.1 workload-analysis skill under `skills/mongodb-workload-analysis/` applies only when using `generate_performance_reports` or writing a customer report from that result. It does not replace instructions for discovery, health checks, or ad hoc PromQL. The report builder attaches `report_validation` to every generated report; this checks required structure while preserving role consistency warnings. Use the JSON validator on structured output and the Markdown validator on a report draft. Structural errors must be resolved; telemetry warnings must remain explicit in the report. The skill’s default tool window is the MCP generator’s seven days; do not imply QAN or other data sources unless they were returned.

When the user requests a Google Docs deliverable, use `generate_performance_reports` for PMM data and the connected Google Drive/Docs tools to create a new native Google Doc for that request. Every new report must be a separate file; never overwrite, reuse, or edit the current or a previous report to deliver a new report. Follow a referenced prior report's format, and include the date, period, environment, available metrics, telemetry gaps, and limitations. Copy the visual template to a new file, preserve the source template and all previous reports, and use a unique title containing the cluster and date/time. `generate_performance_reports` returns structured PMM data; Google Docs creation is provided by the separate Drive connector.

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
