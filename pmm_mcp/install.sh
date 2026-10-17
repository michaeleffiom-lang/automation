#!/usr/bin/env bash
# Install locally without overwriting credentials or client configuration.
set -euo pipefail
project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$project_dir"
python_bin="${PMM_PYTHON:-python3}"
venv_python='.venv/bin/python'

if [[ -x "$venv_python" ]] \
  && "$venv_python" -c 'import sys; sys.exit(sys.version_info < (3, 11))' \
  && "$venv_python" -m pip --version >/dev/null 2>&1; then
  python_bin="$venv_python"
else
  command -v "$python_bin" >/dev/null || { echo 'Instale Python 3.11+ e tente novamente.' >&2; exit 1; }
  "$python_bin" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else "É necessário Python 3.11 ou superior.")'
  if [[ -e .venv ]]; then
    echo 'O ambiente virtual existente está incompleto ou usa Python anterior a 3.11; ele será recriado.' >&2
  fi
  "$python_bin" -m venv --clear .venv || { echo 'Não foi possível criar o ambiente. No Ubuntu/Debian, instale python3-venv.' >&2; exit 1; }
  python_bin="$venv_python"
fi
"$python_bin" -m pip install -e .
if [[ ! -e .env ]]; then
  (umask 077; cp .env.example .env)
fi
.venv/bin/python - <<'PY'
import json
from pathlib import Path
root = Path.cwd()
config = {'mcpServers': {'pmm-operations': {
    'command': str(root / '.venv/bin/pmm-mongodb-discovery'),
    'cwd': str(root),
    'env': {'PMM_CLIENTS_FILE': str(root / 'clients.json')},
}}}
(root / 'mcp-client.local.json').write_text(json.dumps(config, indent=2) + '\n')
PY
if command -v git >/dev/null && [[ -e .git ]]; then
  hooks_path="$(git config --get core.hooksPath || true)"
  existing_hook="$(git rev-parse --git-path hooks/pre-commit)"
  if [[ "$hooks_path" == '.githooks' ]]; then
    echo 'Verificação local do prompt já habilitada.'
  elif [[ -z "$hooks_path" && ! -e "$existing_hook" ]]; then
    git config --local core.hooksPath .githooks
    echo 'Verificação local do prompt habilitada antes dos commits.'
  else
    echo 'Hooks existentes preservados. Integre .githooks/pre-commit ao seu hook para verificar o prompt.'
  fi
fi
cat <<'TEXT'
Instalação concluída.
1. Configure a URL e as credenciais do seu PMM no arquivo .env.
2. Adicione o bloco de mcp-client.local.json à configuração MCP do seu cliente de chat.
3. Use PROMPT.md como instruções do agente (veja docs/agent-instructions.md).
4. Reinicie o servidor MCP no cliente e use: use pmm mcp: liste os MongoDB.
Para atualizar esta instalação: ./update.sh
TEXT
