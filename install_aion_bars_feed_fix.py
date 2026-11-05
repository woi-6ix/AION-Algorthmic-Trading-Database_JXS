"""Backup-first, fail-closed HTTP-400 fix for Alpaca historical options trade bars.

Only edits the historical-bars function, the read-only chart panel, and
an optional preflight diagnostic. Does not change entry/exit logic, state,
credentials, execution journal, webhooks, account selection, or order routes.
"""
from __future__ import annotations

import ast
import os
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PAYLOAD = ROOT / 'aion_bars_feed_payload'
NAMES = ('aion_alpaca_paper_algo.py', 'aion_alpaca_paper_panel.py', 'aion_option_preflight.py')


def bot_armed():
    database = ROOT / 'aion_alpaca_paper_algo.sqlite3'
    if not database.exists():
        return False
    try:
        with sqlite3.connect(f'file:{database.as_posix()}?mode=ro', uri=True, timeout=2) as db:
            found = db.execute("SELECT value FROM cfg WHERE name='armed'").fetchone()
        return found is not None and found[0] == 'true'
    except sqlite3.Error as exc:
        raise RuntimeError('Cannot verify paper-bot armed state; refusing to modify files') from exc


def section(text, a, b):
    if text.count(a) != 1 or text.count(b) != 1:
        raise RuntimeError('Expected chart section not found exactly once; refusing changes')
    start = text.index(a)
    end = text.index(b, start)
    return text[start:end]


def patch_one(name, existing, old, fixed):
    if name == 'aion_alpaca_paper_algo.py':
        a,b = '    def bars(self, symbol, start, end=None):', '\n\ndef broker_account_check('
        if 'MAX_COST = Decimal(\'2500\')' not in existing or 'https://paper-api.alpaca.markets' not in existing:
            raise RuntimeError('Expected paper-only $2,500 KAIROS V5 release not found; no changes made')
    elif name == 'aion_alpaca_paper_panel.py':
        a,b = "@app.get('/api/bars')", '\n\nPAGE = '
    elif name == 'aion_option_preflight.py':
        a,b = 'def run_readonly_chart_probe(', '\n\ndef main()'
    else:
        raise RuntimeError('Unexpected file')

    previous = section(existing,a,b)
    from_old = section(old,a,b)
    to_new = section(fixed,a,b)
    if previous == from_old:
        updated = existing.replace(previous,to_new,1)
    elif previous == to_new:
        updated = existing
    else:
        raise RuntimeError(f'{name} chart section differs from expected release; refusing to overwrite local changes')

    if name == 'aion_alpaca_paper_panel.py':
        old_label = 'Indicative historical options feed; bars can lag by 15 minutes.'
        new_label = 'Historical option trade bars, NOT indicative quotes; Basic-plan bars may lag by 15+ minutes.'
        if old_label in updated:
            updated=updated.replace(old_label,new_label,1)
        elif new_label not in updated:
            raise RuntimeError('Chart UI label changed unexpectedly; refusing overwrite')
        updated=updated.replace('3-minute option premium chart', '3-minute option trade price chart')
    if name == 'aion_option_preflight.py':
        updated=updated.replace('AION SPY option chart and KAIROS V5 PAPER readiness preflight.',
                                'AION SPY option historical trade-bars and KAIROS V5 PAPER readiness preflight.')
    ast.parse(updated, filename=name)
    return updated


def install():
    if bot_armed():
        raise RuntimeError('Paper bot remains ARMED: disarm, stop its worker, and retry')
    staged={}
    for name in NAMES:
        target=ROOT / name
        original=PAYLOAD / 'original' / name
        fixed=PAYLOAD / 'fixed' / name
        if not target.is_file() or not original.is_file() or not fixed.is_file():
            raise RuntimeError(f'Missing {name}. Extract the full ZIP into your existing AION-Execution directory')
        current=target.read_text(encoding='utf-8')
        desired=patch_one(name,current,original.read_text(encoding='utf-8'),fixed.read_text(encoding='utf-8'))
        staged[target]=desired
    if all(path.read_text(encoding='utf-8')==value for path,value in staged.items()):
        print('ALREADY INSTALLED: Alpaca historical options-bars feed fix.')
        return
    backup=ROOT / 'aion-options-bar-backups' / datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    backup.mkdir(parents=True,exist_ok=False)
    for path in staged: shutil.copy2(path,backup / path.name)
    changed=[]
    try:
        for path,content in staged.items():
            if path.read_text(encoding='utf-8')==content: continue
            temporary=path.with_name(path.name+'.aion-bars-tmp')
            temporary.write_text(content,encoding='utf-8')
            os.replace(temporary,path)
            changed.append(path)
    except Exception:
        for path in changed: shutil.copy2(backup/path.name,path)
        raise
    print('SUCCESS: Removed invalid historical-options-bars feed parameter; chart/preflight updated.')
    print('No trading API calls made. No changes to credentials, orders, risk limits, journals, or databases.')
    print('Backups:',backup)
    print('Restart dashboard 8501; keep the PAPER bot DISARMED until GET chart and webhook checks pass.')

if __name__ == '__main__':
    try:install()
    except Exception as exc:
        print('NO PATCH APPLIED:',str(exc),file=sys.stderr)
        sys.exit(2)