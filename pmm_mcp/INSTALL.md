# Instalação e atualização do PMM Operations MCP

## Pré-requisitos

- Linux, macOS ou Windows com WSL, usando Bash.
- Python 3.11 ou superior, com `pip` e suporte a `venv`.
- Git; GitHub CLI (`gh`) é opcional.
- Acesso ao repositório privado `adamotonete/pmm_mcp`: aceite o convite do GitHub antes de clonar.
- Um cliente de chat compatível com MCP via `stdio` e acesso de rede ao PMM que será consultado.
- Acesso à internet para baixar o projeto e as dependências Python.
- Docker Engine e Docker Compose v2, se você também quiser executar o laboratório local de PMM.

## 1. Baixar o projeto

Escolha uma das opções. Execute os comandos na pasta em que deseja instalar o projeto.

### Opção A: GitHub CLI (`gh`)

Autentique sua própria conta e configure as credenciais HTTPS do Git:

```bash
gh auth login --hostname github.com --git-protocol https --web
gh auth setup-git
gh repo clone adamotonete/pmm_mcp
cd pmm_mcp
```

### Opção B: Git (`git`)

Esta opção pressupõe que a autenticação HTTPS do GitHub já esteja configurada no gerenciador de credenciais do seu computador, com uma conta que tenha acesso ao repositório. Não inclua credenciais na URL de clonagem. Se ainda não tiver autenticação configurada, use a opção com `gh` acima.

```bash
git clone https://github.com/adamotonete/pmm_mcp.git
cd pmm_mcp
```

## 2. Instalar

Dentro da pasta clonada:

```bash
./install.sh
```

O instalador:

- Cria o ambiente virtual `.venv` e instala o MCP e suas dependências.
- Cria `.env` a partir de `.env.example` somente se ele ainda não existir.
- Gera `mcp-client.local.json` com os caminhos absolutos da sua instalação.
- Habilita a verificação de atualização do prompt antes dos commits quando não há hooks personalizados.

É possível executar o instalador novamente: `.env` e `clients.json` são preservados. O arquivo `mcp-client.local.json` é gerado novamente a cada execução.

Para selecionar outro executável Python:

```bash
PMM_PYTHON=python3.12 ./install.sh
```

Essa opção seleciona o Python usado para criar ou reparar um ambiente novo; não troca a versão de um `.venv` existente que esteja íntegro.

## 3. Configurar o PMM

Abra `.env` no seu editor e ajuste os valores para o seu ambiente:

- `PMM_SERVICE_DISCOVERY_URL`: URL completa, incluindo `/prometheus/service-discovery`.
- `PMM_API_BASE_URL`: URL da API do mesmo PMM, incluindo `/v1`.
- `PMM_DISCOVERY_USERNAME` e `PMM_DISCOVERY_PASSWORD`: credenciais do PMM, quando usar autenticação por usuário e senha.
- `PMM_DISCOVERY_TOKEN`: alternativa para autenticação por token. Nesse caso, deixe vazios os campos de usuário e senha de descoberta e também `PMM_ADMIN_USERNAME` e `PMM_ADMIN_PASSWORD`.
- `PMM_TLS_INSECURE`: use `false` para validar certificados confiáveis; `true` permite certificados autoassinados em um ambiente de desenvolvimento autorizado.

Os valores do arquivo de exemplo não dão acesso a um PMM real. Mantenha credenciais apenas na configuração local.

### Laboratório local opcional

O arquivo `docker-compose.mcp.yml` inicia um PMM Server 3, um replica set MongoDB 7 de três nós (`rs0`) e um agente PMM dedicado para cada nó, em uma rede privada criada pelo Compose. Depois de instalar Docker, execute:

```bash
bash scripts/prepare-mongodb-keyfile.sh
docker compose --env-file .env -f docker-compose.mcp.yml up -d
docker compose --env-file .env -f docker-compose.mcp.yml ps
```

Acesse o PMM em `https://127.0.0.1:8443`, usando `PMM_ADMIN_USERNAME` e `PMM_ADMIN_PASSWORD` do `.env`. O certificado local é autoassinado; para este laboratório, mantenha `PMM_TLS_INSECURE=true` no cliente MCP. Os clientes `pmm-client-mcp`, `pmm-client-mcp-rs2` e `pmm-client-mcp-rs3` registram cada membro de `rs0` como um nó PMM separado. Consulte os logs com `docker compose --env-file .env -f docker-compose.mcp.yml logs -f pmm-server-2 pmm-client-mcp pmm-client-mcp-rs2 pmm-client-mcp-rs3`. Para parar sem remover os dados, execute `docker compose --env-file .env -f docker-compose.mcp.yml down`.

O serviço `mongodb7-rs-init` inicializa o replica set automaticamente. Para carregar somente o banco oficial `sample_mflix`, execute:

```bash
bash scripts/load-mflix.sh
```

Com Podman, use `CONTAINER_CLI=podman bash scripts/load-mflix.sh`. O script baixa o arquivo temporário e restaura as coleções `sample_mflix.*`; o arquivo não é armazenado no repositório.

Para múltiplos clientes, copie o exemplo e edite os ambientes e as variáveis de credenciais referenciadas:

```bash
cp clients.example.json clients.json
```

Execute essa cópia somente se ainda não houver um `clients.json` que precise preservar. Consulte a seção [Clientes PMM do README](README.md#clientes-pmm) para o formato.

## 4. Configurar o cliente MCP e o agente

1. Abra o arquivo gerado `mcp-client.local.json`.
2. Adicione a entrada `pmm-operations` à configuração MCP do seu cliente de chat, preservando os outros servidores existentes. Se o cliente usar outro formato, utilize os valores gerados de `command`, `cwd` e `env` nos campos equivalentes.
3. Crie ou configure o agente **PMM Operations** e associe esse servidor MCP.
4. Copie o conteúdo integral de [PROMPT.md](PROMPT.md) para as instruções do agente. Se o cliente aceitar instruções por arquivo, aponte para o arquivo local.
5. Reinicie o servidor MCP no cliente de chat.

Os campos `command`, `cwd` e o caminho de `PMM_CLIENTS_FILE` são calculados a partir da pasta em que você executou o instalador. Não existe um caminho fixo de outro usuário. Não copie o JSON gerado em outra máquina; gere o seu com `./install.sh`.

O instalador não altera automaticamente a configuração do cliente de chat. O servidor usa `stdio` e deve ser iniciado pelo cliente MCP. Veja também a [definição do agente](docs/agent-instructions.md).

## 5. Verificar a conexão

No chat, envie:

```text
use pmm mcp: liste os MongoDB do cliente
```

Informe a URL do PMM quando solicitada. Para habilitar o uso nos próximos pedidos PMM da mesma sessão:

```text
A partir de agora use pmm mcp:
```

O health check padrão ainda retorna `not_configured`, pois suas regras corporativas não foram definidas. Isso não indica falha na instalação.

## Atualizar uma instalação existente

Dentro da pasta clonada, independentemente de ter usado `git` ou `gh`:

```bash
./update.sh
```

O comando baixa as atualizações da branch acompanhada pelo clone com `git pull --ff-only` e executa `install.sh`. `.env` e `clients.json` são preservados. Se houver alterações locais ou histórico divergente, a atualização para sem descartar arquivos.

Depois de atualizar:

1. Reinicie o servidor MCP no cliente de chat.
2. Recarregue `PROMPT.md` ou copie novamente seu conteúdo para as instruções do agente.
3. Ative novamente a sessão PMM, se desejar usar pedidos sem prefixo.

Para reinstalar o código local sem baixar commits, execute apenas `./install.sh`.

## Problemas comuns

- **Repositório não encontrado ou acesso negado:** confirme que aceitou o convite e que a conta autenticada tem acesso. Se usar `gh`, confira com `gh auth status`.
- **Falha ao criar `.venv`:** confirme Python 3.11+; no Ubuntu/Debian, pode ser necessário instalar o pacote `python3-venv` correspondente à versão usada. Se o ambiente existente estiver incompleto (por exemplo, sem `pip`) ou usar uma versão anterior, o instalador o recria automaticamente sem alterar `.env` ou `clients.json`.
- **Atualização interrompida por alterações locais:** confira `git status` e faça commit ou guarde seu trabalho antes de executar `./update.sh` novamente.
- **Cliente não encontra o executável:** confira os caminhos em `mcp-client.local.json`. Se mover a instalação, recrie o ambiente virtual no destino e execute o instalador; ambientes virtuais não são portáteis entre pastas ou máquinas.
- **Falha ao conectar ao PMM:** confira URL, conectividade, credenciais e certificado em `.env`; reinicie o MCP após alterar a configuração.

Para validar o código local, execute os testes:

```bash
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v
```
