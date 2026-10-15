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
4. Inicie a conversa com `use pmm mcp: liste os MongoDB do cliente`.
5. Depois de atualizar o repositório, recarregue o arquivo ou copie novamente o prompt para o cliente.

For a periodic performance assessment, confirm the environment and use `generate_performance_reports`. The tool collects available PMM series by cluster/replica set and returns a structured assessment; it does not replace the standard `health_check`, and missing metrics must not be interpreted as normal conditions. All reports and documents produced by the agent must be written in English, even when the conversation is in Portuguese.

When the user requests a Google Docs deliverable, use `generate_performance_reports` for PMM data and the connected Google Drive/Docs tools to create a native Google Doc. Follow a referenced prior report's format, and include the date, period, environment, available metrics, telemetry gaps, and limitations. `generate_performance_reports` returns structured PMM data; Google Docs creation is provided by the separate Drive connector.

Use the full report `Percona Managed Services Assessment — mflix-lab rs0 — 7-Day Review — 2026-10-15` as the default visual template. Verify the full version by its logo, tables, and section structure if a short draft with the same title appears in Drive. Copy the template for each new report and preserve its layout while replacing all dates and report-specific content.

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
