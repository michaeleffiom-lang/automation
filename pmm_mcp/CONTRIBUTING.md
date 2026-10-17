# Contribuir

Toda entrega deve atualizar [PROMPT.md](PROMPT.md), incluindo mudanças no servidor, agente, documentação, testes e configuração. Ajuste as instruções que mudaram; quando a operação permanecer igual, registre na revisão do prompt o motivo e a alteração entregue.

Antes de abrir um pull request:

```bash
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v
python3 scripts/check_prompt_updated.py origin/main
```

O comando compara a base informada com os arquivos locais, incluindo arquivos novos não ignorados. Em CI, a comparação usa os commits base e final do push ou pull request. Exclusões e renomeações também contam como mudanças.

O instalador habilita `.githooks/pre-commit` quando não há hooks personalizados. Ele verifica o conteúdo preparado para commit e recusa alterações sem atualização do prompt. Hooks existentes são preservados; nesse caso, integre a chamada manualmente. A verificação pode ser ignorada com `--no-verify`, por isso também deve fazer parte da revisão.

O modelo [docs/github-actions-checks.yml](docs/github-actions-checks.yml) permite executar a verificação em pushes e pull requests. Para habilitá-lo, copie-o para `.github/workflows/checks.yml` e publique com uma autenticação que tenha a permissão `workflow`. Ele ainda não está ativo no GitHub. Para impedir merges com falha, configure a proteção da branch exigindo `prompt-sync` após habilitá-lo.

A verificação confere presença da atualização, não sua qualidade: a revisão humana deve conferir a coerência com a implementação.

Segredos e configurações reais permanecem locais. Ao compartilhar uma instalação, envie o link do repositório e os arquivos de exemplo, nunca `.env`, `clients.json` ou o cache.
