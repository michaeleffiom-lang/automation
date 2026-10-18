#!/usr/bin/env python3
"""Check structure and evidence ID referential integrity of a generated Markdown report.

This script does NOT verify the truth of performance claims, metric units, source URLs,
customer identity, quality of correlations, or whether recommendations are justified.
"""
from __future__ import annotations
import argparse
import re
import sys
from pathlib import Path

REQUIRED = [
    'Executive summary', 'Workload overview', 'Key findings',
    'Recommendations and follow-up', 'Monitoring gaps and limitations',
    'Evidence register', 'Report scope and limitations',
]


def validate(content: str) -> list[str]:
    errors: list[str] = []
    heads = set(re.findall(r'^## (.+?)\s*$', content, re.M))
    for heading in REQUIRED:
        if heading not in heads:
            errors.append(f'Missing required heading: ## {heading}')
    if not re.search(r'Reporting window \(UTC\):', content):
        errors.append('Missing UTC reporting window')
    if not re.search(r'\*\*Coverage:\*\*', content):
        errors.append('Missing monitoring coverage statement')
    if not re.search(r'^### F-\d{3}', content, re.M):
        key_findings = re.search(r'^## Key findings\s*$([\s\S]*?)(?=^## |\Z)', content, re.M)
        if not key_findings or not re.search(
            r'no (?:material|reportable) findings (?:were|are) identified',
            key_findings.group(1),
            re.I,
        ):
            errors.append('Add numbered F-xxx findings or explicitly state that no material findings were identified')
    sections = re.split(r'^### (?=F-\d{3}\b)', content, flags=re.M)
    findings = [s for s in sections[1:] if re.match(r'F-\d{3}\b', s)]
    references: set[str] = set()
    for section in findings:
        fid = re.match(r'(F-\d{3})', section).group(1)
        for field in ['Finding ID', 'Evidence', 'Observation', 'Correlation', 'Interpretation',
                      'Alternative explanation', 'Optimization layer', 'Status', 'Confidence', 'Customer impact',
                      'Recommendation', 'Validation / completion']:
            if not re.search(r'\*\*' + re.escape(field) + r':\*\*', section):
                errors.append(f'{fid}: missing field {field}')
        ematch = re.search(r'\*\*Evidence:\*\*([^\n]+)', section)
        if ematch:
            ids = set(re.findall(r'E-\d{3}', ematch.group(1)))
            if not ids:
                errors.append(f'{fid}: no E-xxx evidence IDs')
            references |= ids
    m = re.search(r'^## Evidence register\s*$([\s\S]*?)(?=^## |\Z)', content, re.M)
    register = m.group(1) if m else ''
    table_rows = [line for line in register.splitlines() if line.startswith('|')]
    evidence_ids: set[str] = set()
    for row in table_rows:
        first = row.strip().split('|')[1].strip() if len(row.split('|')) > 2 else ''
        if re.fullmatch(r'E-\d{3}', first):
            evidence_ids.add(first)
    if references - evidence_ids:
        errors.append('Finding evidence IDs missing from register: ' + ', '.join(sorted(references-evidence_ids)))
    if '<' in content and re.search(r'<(?:Customer|cluster|start UTC|end UTC|actual|node|metric)', content, re.I):
        errors.append('Possible unresolved template placeholders')
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('report', type=Path, help='Customer report Markdown path')
    args = parser.parse_args()
    if not args.report.is_file():
        parser.error(f'Not a file: {args.report}')
    errors = validate(args.report.read_text(encoding='utf-8'))
    if errors:
        for issue in errors:
            print('ERROR:', issue, file=sys.stderr)
        print(f'Validation failed: {len(errors)} issue(s)', file=sys.stderr)
        return 1
    print('PASS: Report structure and evidence IDs look valid. Manual technical review still required.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
