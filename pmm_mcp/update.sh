#!/usr/bin/env bash
# Update this checkout and reinstall using the existing local configuration.
set -euo pipefail
project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$project_dir"
command -v git >/dev/null || { echo 'Instale Git para atualizar.' >&2; exit 1; }
if [[ -n "$(git status --porcelain)" ]]; then
  echo 'Existem alterações locais. Faça commit ou guarde-as antes de atualizar; nenhum arquivo foi descartado.' >&2
  exit 1
fi
# Support older clones with an SSH origin using GitHub CLI HTTPS credentials.
git -c 'url.https://github.com/.insteadOf=git@github.com:' pull --ff-only
"$project_dir/install.sh"
echo 'Atualização concluída. Reinicie o MCP e recarregue PROMPT.md nas instruções do agente. Ative novamente a sessão PMM se necessário.'
