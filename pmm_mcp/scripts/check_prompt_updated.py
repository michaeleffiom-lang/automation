"""Require a prompt update alongside any repository change (stdlib only)."""

import argparse
import subprocess
from pathlib import Path


def git(*args: str, cwd: Path | None = None) -> str:
    return subprocess.check_output(['git', *args], cwd=cwd, text=True).strip()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('base', help='Base commit; all zeros means a new branch')
    parser.add_argument('head', nargs='?', help='Final commit; omit to check local files')
    args = parser.parse_args()
    repo_root = Path(git('rev-parse', '--show-toplevel')).resolve()
    project_root = Path(__file__).resolve().parent.parent
    try:
        project_path = project_root.relative_to(repo_root)
    except ValueError as exc:
        raise SystemExit(f'Prompt checker must be inside its Git working tree: {exc}')
    prompt_path = (project_path / 'PROMPT.md').as_posix()
    prefix = '' if project_path == Path('.') else project_path.as_posix().rstrip('/') + '/'

    def project_files(paths: set[str]) -> set[str]:
        if not prefix:
            return paths
        return {path[len(prefix):] for path in paths if path.startswith(prefix)}

    base = args.base
    if base and set(base) == {'0'}:
        base = subprocess.check_output(
            ['git', 'hash-object', '-t', 'tree', '--stdin'],
            cwd=repo_root,
            input='',
            text=True,
        ).strip()
    revisions = [base, args.head] if args.head else [base]
    changed = set(git('diff', '--no-renames', '--name-only', '-z', *revisions, cwd=repo_root).split('\0')) - {''}
    if not args.head:
        changed.update(git('ls-files', '--others', '--exclude-standard', '-z', cwd=repo_root).split('\0'))
        changed.discard('')
    changed = project_files(changed)
    if args.head:
        prompt = git('show', f'{args.head}:{prompt_path}', cwd=repo_root)
    else:
        prompt_file = project_root / 'PROMPT.md'
        prompt = prompt_file.read_text() if prompt_file.exists() else ''
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
