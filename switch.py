"""Local Codex thread model migration. Python standard library only."""
import argparse
import datetime
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import occupancy


def connect(path, writable=False):
    return sqlite3.connect(path.resolve().as_uri() + ('?mode=rw' if writable else '?mode=ro'), uri=True, timeout=5)


def stopped(db):
    items=occupancy.holders(db)
    if items: raise RuntimeError('Database in use: '+occupancy.describe(items))


def backup(c, db):
    directory = db.parent / 'gpt-switch-backups' / datetime.datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    directory.mkdir(parents=True)
    target = directory / db.name
    with sqlite3.connect(target) as dest:
        c.backup(dest)
        if dest.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise RuntimeError('Backup integrity check failed; no changes made.')
    print('BACKUP:', target)
    return directory


def main():
    p = argparse.ArgumentParser(description='Preview by default. Quit Codex before --apply. Never reads API keys.')
    p.add_argument('--db', type=Path)
    p.add_argument('--list', action='store_true', help='List IDs and model/provider (no conversation text)')
    p.add_argument('--reference', help='Copy model/provider from this working thread ID')
    p.add_argument('--model')
    p.add_argument('--provider')
    p.add_argument('--thread', action='append', help='Target thread ID; repeat for multiple threads')
    p.add_argument('--from-provider', help='Select all threads belonging to this provider')
    p.add_argument('--apply', action='store_true')
    a = p.parse_args()
    if not a.db:
        files = list(Path(os.environ.get('CODEX_HOME', str(Path.home()/'.codex'))).glob('state_*.sqlite'))
        if len(files) != 1:
            p.error('Expected exactly one state database. Specify --db explicitly.')
        a.db = files[0]
    with connect(a.db) as c:
        c.row_factory = sqlite3.Row
        columns = {r[1] for r in c.execute('PRAGMA table_info(threads)')}
        if not {'id', 'model', 'model_provider'} <= columns:
            raise RuntimeError('Unsupported schema: threads requires id, model, model_provider. No changes made.')
        rows = [dict(r) for r in c.execute('SELECT id, model, model_provider FROM threads')]
    if a.list:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return
    if a.reference:
        ref = next((r for r in rows if r['id'] == a.reference), None)
        if not ref:
            p.error('Reference thread not found')
        a.model, a.provider = ref['model'], ref['model_provider']
    if not a.model or not a.provider:
        p.error('Use --reference WORKING_ID or both --model and --provider')
    if not a.thread and a.from_provider is None:
        p.error('Explicit selection required: --thread ID or --from-provider NAME')
    if a.thread and set(a.thread) - {r['id'] for r in rows}:
        p.error('One or more target thread IDs do not exist')
    selected = [r for r in rows if (not a.thread or r['id'] in a.thread) and (a.from_provider is None or r['model_provider'] == a.from_provider) and (r['model'], r['model_provider']) != (a.model, a.provider)]
    print(json.dumps({'database': str(a.db), 'target': [a.model, a.provider], 'changes': selected}, ensure_ascii=False, indent=2))
    if not a.apply or not selected:
        print('Preview only; use --apply after quitting Codex.' if not a.apply else 'No changes required.')
        return
    stopped(a.db)
    with connect(a.db, True) as c:
        directory = backup(c, a.db)
        (directory/'changes.json').write_text(json.dumps({'target': [a.model, a.provider], 'before': selected}, ensure_ascii=False, indent=2), encoding='utf-8')
        c.execute('BEGIN IMMEDIATE')
        for r in selected:
            result = c.execute('UPDATE threads SET model=?, model_provider=? WHERE id=? AND model IS ? AND model_provider IS ?', (a.model, a.provider, r['id'], r['model'], r['model_provider']))
            if result.rowcount != 1:
                raise RuntimeError('Thread changed concurrently; transaction rolled back.')
        c.commit()
        print('Updated', len(selected), 'threads. Restart Codex.')


if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        print('ERROR:', e, file=sys.stderr)
        sys.exit(1)
