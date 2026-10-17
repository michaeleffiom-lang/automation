"""Require a prompt update alongside any repository change (stdlib only)."""

import argparse
import subprocess
from pathlib import Path


def git(*args: str) -> str:
    return subprocess.check_output(['git', *args], text=True).strip()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('base', help='Base commit; all zeros means a new branch')
    parser.add_argument('head', nargs='?', help='Final commit; omit to check local files')
    args = parser.parse_args()
    base = args.base
    if base and set(base) == {'0'}:
        base = subprocess.check_output(
            ['git', 'hash-object', '-t', 'tree', '--stdin'], input='', text=True
        ).strip()
    revisions = [base, args.head] if args.head else [base]
    changed = set(git('diff', '--no-renames', '--name-only', '-z', *revisions).split('\0')) - {''}
    if not args.head:
        changed.update(git('ls-files', '--others', '--exclude-standard', '-z').split('\0'))
        changed.discard('')
    if args.head:
        prompt = git('show', f'{args.head}:PROMPT.md')
    else:
        root = Path(git('rev-parse', '--show-toplevel'))
        prompt = (root / 'PROMPT.md').read_text() if (root / 'PROMPT.md').exists() else ''
    if not prompt.strip():
        print('ERRO: PROMPT.md deve existir e conter as instruções do agente.')
        return 1
    if changed - {'PROMPT.md'} and 'PROMPT.md' not in changed:
        print('ERRO: atualize PROMPT.md na mesma entrega das demais modificações.')
        return 1
    print('OK: atualização do prompt verificada.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
