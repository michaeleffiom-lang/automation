# Arquitetura inicial

## Camadas

```text
Usuário
  |
  v
Agente / LLM
  |
  v
MCP PMM Operations
  +-- Configuração multi-cliente
  +-- Cliente PMM somente leitura
  +-- Descoberta normalizada
  +-- Workflows padrão
  +-- Investigação PromQL controlada
  |
  v
PMM / Inventário / Prometheus API
```

## Conexões por cliente

Cada cliente tem um identificador lógico e uma conexão independente em `clients.json`. Endereços, portas encaminhadas e credenciais não são inferidos de outro cliente. Credenciais devem ser referenciadas por variáveis de ambiente.

O cliente PMM usa:

- `/v1/inventory/services` para identidade de serviços;
- `/v1/inventory/agents` para relacionar exporters, status e modo push/pull;
- `/prometheus/service-discovery` para targets e labels quando disponíveis;
- `/prometheus/api/v1/label/__name__/values` para descobrir métricas;
- `/prometheus/api/v1/query` e `/query_range` para investigação.

## Modelo normalizado

O identificador estável de um serviço é `service_id`. O nome amigável é `service_name`. Um serviço pode ser relacionado a `node_id`, `cluster`, `replication_set` e uma lista de agentes.

O MCP não devolve senhas, tokens ou credenciais nos resultados.

## Standard mode

O `health_check` terá regras fixas, versão de regras, escopo, checks e resumo. A LLM interpreta o resultado, mas não escolhe nem altera o conjunto de métricas padrão.

Como as métricas e limites ainda não foram definidos, a implementação atual retorna `status=not_configured` e não apresenta um diagnóstico falso.

## Investigation mode

`discover_prometheus_metrics` e `query_prometheus` são ferramentas exploratórias. A consulta é somente leitura e possui limites de tamanho, duração do `step`, caracteres de controle e quantidade de resultados.

O agente deve usar investigação para perguntas fora do health check e deixar claro quando o resultado não pertence ao relatório padrão.
