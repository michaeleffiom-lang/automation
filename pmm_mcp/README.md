# PMM Operations MCP

MCP para descobrir e investigar ambientes MongoDB monitorados pelo Percona PMM.

## Compartilhar e instalar o agente

Repositório: [adamotonete/pmm_mcp](https://github.com/adamotonete/pmm_mcp).
Em um repositório privado, cada usuário precisa receber acesso pelo GitHub antes de clonar.

- [INSTALL.md](INSTALL.md): instalação usando `git` ou `gh`, configuração e atualização.
- [PROMPT.md](PROMPT.md): prompt completo para copiar para o cliente de chat.
- [Definição do agente](docs/agent-instructions.md): configuração do agente PMM Operations.
- [Skill MongoDB Workload Analysis v1.1](skills/mongodb-workload-analysis/SKILL.md): metodologia e evidência aplicadas somente ao fluxo `generate_performance_reports`, com template e validadores.
- [CONTRIBUTING.md](CONTRIBUTING.md): atualização obrigatória do prompt e validação.

O MCP descobre os serviços pelo inventário do PMM configurado por cada usuário. Não inclui acesso a uma instância PMM nem configurações reais de clientes.

O endpoint `prometheus/service-discovery` é consultado como complemento. Serviços cujos agentes usam métricas `push` podem não aparecer nessa página, mas continuam identificáveis por `service_id` e `service_name` no inventário.

## Executar

Pré-requisitos: Linux, macOS ou WSL com Bash, Python 3.11+ e Git. O exemplo abaixo usa GitHub CLI (`gh`); para instalar somente com Git e autenticação HTTPS já configurada, siga [INSTALL.md](INSTALL.md). Cada colaborador precisa de acesso ao repositório privado e deve usar sua própria conta.

Na primeira instalação:

```bash
gh auth login
gh auth setup-git
gh repo clone adamotonete/pmm_mcp
cd pmm_mcp
./install.sh
```

O instalador cria `.venv`, instala o MCP, cria `.env` com exemplos somente se ele não existir e gera `mcp-client.local.json` com os caminhos absolutos desta máquina. Ele pode ser executado novamente e preserva `.env` e `clients.json`; se o ambiente virtual estiver incompleto ou usar Python anterior a 3.11, ele será recriado.

Depois da instalação:

1. Edite `.env` com a URL e as credenciais do seu PMM.
2. Copie o bloco de `mcp-client.local.json` para a configuração MCP do cliente de chat.
3. Cole [PROMPT.md](PROMPT.md) nas instruções do agente, conforme a [definição do agente](docs/agent-instructions.md).
4. Reinicie o MCP no cliente de chat.

Para atualizar, dentro da pasta clonada:

```bash
./update.sh
```

O atualizador baixa os commits com `git pull --ff-only` e executa o instalador. Ele interrompe se houver alterações locais ou histórico divergente, sem descartar trabalho. Depois, reinicie o MCP e recarregue o prompt no chat; instruções copiadas não se atualizam automaticamente. Para reinstalar sem baixar commits, execute `./install.sh`.

Se necessário, selecione o Python com `PMM_PYTHON=python3.12 ./install.sh`. No Ubuntu/Debian, a criação do ambiente virtual pode exigir o pacote `python3-venv`.

Para múltiplos clientes, copie `clients.example.json` para `clients.json` e configure as variáveis referenciadas. Os exemplos não contêm credenciais válidas. O Docker Compose é um laboratório opcional que inicia PMM Server, um replica set MongoDB 7 de três nós e o PMM Client na rede `mcp-pmm-net`; não é necessário para executar o MCP. O PMM fica disponível em `https://127.0.0.1:8443` com as credenciais `PMM_ADMIN_USERNAME` e `PMM_ADMIN_PASSWORD` do `.env`.

Para iniciar o laboratório depois de instalar Docker e Docker Compose:

Prepare o keyfile local antes da primeira inicialização:

```bash
bash scripts/prepare-mongodb-keyfile.sh
```

```bash
docker compose --env-file .env -f docker-compose.mcp.yml up -d
docker compose --env-file .env -f docker-compose.mcp.yml ps
```

Na primeira inicialização, aguarde `pmm-server-2` e os três nós MongoDB ficarem saudáveis; o serviço `mongodb7-rs-init` inicializa `rs0`. Três agentes PMM dedicados, um por nó, registram os serviços `mflix-rs`, `mflix-rs2` e `mflix-rs3`, todos no cluster `mflix-lab`. Os dados persistem nos volumes `pmm-server-mcp-data`, `mongodb7-rs1-data`, `mongodb7-rs2-data`, `mongodb7-rs3-data` e nos volumes separados dos três clientes PMM. Para parar o laboratório sem apagar dados, use `docker compose --env-file .env -f docker-compose.mcp.yml down`.

Para baixar e restaurar somente o banco oficial `sample_mflix` no replica set:

```bash
bash scripts/load-mflix.sh
```

Com Podman, use `CONTAINER_CLI=podman bash scripts/load-mflix.sh`. O script baixa o arquivo temporário e usa `mongorestore --nsInclude=sample_mflix.*`.

O servidor usa `stdio`, portanto deve ser iniciado por um cliente MCP. O arquivo `.env` é carregado automaticamente.

Configuração de cliente MCP (substitua os caminhos pelo diretório em que clonou):

```json
{
  "mcpServers": {
    "pmm-operations": {
      "command": "/caminho/absoluto/pmm_mcp/.venv/bin/pmm-mongodb-discovery",
      "cwd": "/caminho/absoluto/pmm_mcp"
    }
  }
}
```

## Ferramentas

- `connect_pmm_client(url, ...)`: conecta em uma URL informada durante a conversa; a conexão fica somente na memória do processo MCP.
- `disconnect_pmm_client(client_id)`: encerra uma conexão temporária.
- `list_pmm_clients`: lista clientes configurados sem credenciais.
- `discover_environment(client_id)`: retorna clusters, replica sets, nós e serviços MongoDB normalizados.
- `discover_mongodb_services(client_id, service_name, node_id, cluster, replication_set)`: lista MongoDB com filtros simples.
- `get_mongodb_service(service, client_id)`: busca por nome, ID, endereço, cluster ou replica set.
- `discover_prometheus_metrics(client_id, search, limit, category)`: lista nomes do catálogo local; `category` pode ser `all`, `hardware` ou `mongodb`.
- `query_prometheus(client_id, query, start, end, step, max_results)`: executa PromQL somente leitura.
- `compare_workload_windows(client_id, queries, baseline_start, baseline_end, comparison_start, comparison_end, step, change_threshold_percent)`: compara expressões PromQL em duas janelas e retorna estatísticas, deltas e métricas alteradas para investigação.
- `generate_performance_reports(client_id, period_hours, end, step, cluster, replication_set)`: gera uma avaliação de performance por replica set, agrupada por cluster, com condição observada, avaliação Percona MS, relevância ao cliente, foco recomendado e lacunas de telemetria explícitas. Quando disponível, também consulta o estado dos membros e retorna `consistency_check` para sinalizar cobertura de papéis ausente/conflitante ou uma contagem diferente de um primary. Os papéis são um snapshot, não histórico de eleições nem declaração de saúde. O período padrão é de sete dias. Sem `step` explícito, usa 1 minuto até 24 horas, 5 minutos até 7 dias, 15 minutos até 30 dias e 1 hora acima disso, respeitando a retenção, o scrape interval ativo e o limite de pontos. Relatórios indicam contagens reais de amostras; lacunas não são interpoladas e máximos downsampled não representam picos instantâneos exatos. Picos relevantes em métricas de workload recebem uma consulta curta com resolução mais fina quando os dados e o scrape interval permitem.
- `refresh_dashboard_catalog(client_id)`: atualiza explicitamente o catálogo de dashboards, gráficos e métricas.
- `list_grafana_dashboards(client_id, category, search, limit)`: lista dashboards Grafana em cache (`hardware`, `mongodb` ou `all`).
- `get_grafana_dashboard(dashboard_uid, client_id)`: retorna painéis, métricas e PromQL em cache de um dashboard.
- `discover_dashboard_metrics(client_id, category, search, dashboard_uid, limit)`: mostra onde cada métrica aparece nos gráficos.
- `health_check(client_id, ...)`: retorna o contrato padrão com status `not_configured` até as métricas e limites corporativos serem definidos.
- `pmm://clients` e `pmm://mongodb-services/{client_id}`: recursos JSON para clientes MCP que preferem leitura de recursos.

## Conversa natural com um agente

O MCP é o servidor de ferramentas; o chat/agente compatível com MCP é quem conversa com a pessoa. Configure o servidor no cliente de chat usando o bloco acima e dê ao agente estas regras (também disponíveis em [docs/agent-instructions.md](docs/agent-instructions.md)):

Ao executar `connect_pmm_client`, o servidor faz o prefetch dos nomes de métricas Prometheus e dos dashboards Grafana de hardware/MongoDB. O catálogo fica em memória durante a sessão e em `.pmm-cache/` para reutilização. Use `refresh_dashboard_catalog` somente quando quiser atualizar os dados.

Para chamar explicitamente este MCP no Codex, use o prefixo. Sem esse prefixo, o MCP não deve ser usado automaticamente:

```text
use pmm mcp: faça um health check
use pmm mcp: liste todos os MongoDB
```

Para ativá-lo uma única vez na conversa, use:

```text
A partir de agora use pmm mcp:
```

O agente deverá chamar `activate_pmm_mcp`; depois disso, os pedidos PMM da mesma sessão podem ser feitos normalmente. Para voltar ao comportamento padrão, use `deactivate_pmm_mcp`. A ativação é somente de sessão e precisa ser repetida depois que o Codex/MCP for reiniciado.

Use o conteúdo integral de [PROMPT.md](PROMPT.md) nas instruções do agente. Depois de cada atualização do repositório, recarregue o prompt no cliente de chat.

Exemplos de pedidos ao agente:

```text
Liste todos os MongoDB do cliente local-pmm-8443.
Mostre os serviços do cluster sharded-cluster.
Detalhe o serviço standalone-node00.
Quais métricas MongoDB estão disponíveis para investigação?
Liste os dashboards MongoDB disponíveis e mostre o UID do dashboard de replica set.
Abra o dashboard mongodb-instance-overview e mostre as consultas dos seus gráficos.
Execute uma consulta PromQL instantânea para o serviço selecionado.
Compare o workload entre duas janelas, por exemplo:

```text
Compare estas métricas entre 2026-09-10T10:00:00Z--2026-09-10T11:00:00Z e 2026-10-06T10:00:00Z--2026-10-06T11:00:00Z:
- ops_per_second: rate(mongodb_op_counters_total[5m])
- connections: mongodb_connections
```

O agente deve transformar cada item em `queries` com `name` e `query`, informar o percentual mínimo de mudança quando necessário e interpretar o resultado como investigação, não como o health check padrão.

Para uma revisão periódica semelhante a uma avaliação de performance do MongoDB, peça por exemplo:

```text
Gere a avaliação de performance dos últimos sete dias para todos os replica sets.
```

O agente deve confirmar o ambiente, explicar que investigará recursos, armazenamento, conexões e replicação com PromQL somente leitura e chamar `generate_performance_reports`. O resultado é estruturado para uma revisão Percona Managed Services; ele não gera um PDF, não inclui histórico de alertas e não substitui o `health_check` corporativo. Revise `consistency_check` e apresente papéis ausentes ou conflitantes como limitações, nunca como condição saudável.

O pacote `skills/mongodb-workload-analysis/` aplica a metodologia v1.1 somente ao fluxo `generate_performance_reports`; ela não altera health checks, descoberta nem consultas ad hoc. Inclui regras de evidência, referências, template Markdown e dois validadores: `scripts/validate-report.py` para JSON estruturado e `scripts/validate_report.py` para o rascunho Markdown. O servidor também anexa `report_validation` e `consistency_check` nas avaliações. Registre a skill no cliente de chat quando houver suporte a Agent Skills; `PROMPT.md` mantém as instruções essenciais. Os validadores verificam estrutura e referências de evidência, não a veracidade da análise.

```

Toda solicitação de avaliação de performance deve resultar em um Google Docs nativo; o usuário não precisa pedir esse formato separadamente. O agente usa `generate_performance_reports` para coletar os dados PMM e, em seguida, o conector Google Drive/Docs para copiar o modelo visual aprovado em um arquivo novo e exclusivo. O documento preserva logo, hierarquia, tabelas e configuração da página, substitui conteúdo e datas do exemplo, inclui contagens reais de amostras, lacunas e limitações e é verificado antes da entrega. Relatórios anteriores e o modelo nunca são editados ou sobrescritos. Se o conector não estiver disponível, o agente informa que o documento não foi criado e que a entrega está incompleta; a resposta JSON do PMM não é apresentada como substituta.

## Clientes PMM

`clients.json` não contém senhas e é ignorado pelo Git. Cada cliente referencia credenciais por variáveis de ambiente:

```json
{
  "clients": {
    "cliente-a": {
      "base_url": "https://127.0.0.1:18443",
      "service_discovery_url": "https://127.0.0.1:18443/prometheus/service-discovery",
      "auth": {
        "username_env": "PMM_CLIENTE_A_USERNAME",
        "password_env": "PMM_CLIENTE_A_PASSWORD"
      },
      "tls_insecure": true
    }
  }
}
```

Use [clients.example.json](clients.example.json) como referência. Para certificados autoassinados em desenvolvimento, configure `tls_insecure: true`; em produção, use certificados confiáveis.

## Arquitetura

Veja [docs/architecture.md](docs/architecture.md) e o contrato em [docs/health-check-contract.md](docs/health-check-contract.md).

## Testes

```bash
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v
```
