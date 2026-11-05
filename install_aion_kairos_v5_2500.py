"""Backup-first install of KAIROS V5 $2500 Alpaca PAPER executor upgrades.

Never mutates API keys, environment, SQLite data or running paper orders.
Refuses while existing paper bot remains ARMED.
"""
from __future__ import annotations

import ast
import os
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

ROOT=Path(__file__).resolve().parent
SOURCE=ROOT/'paper_v5_2500_payload'
FILES=('aion_alpaca_paper_algo.py','aion_kairos_server.py','aion_execution_journal.py','aion_alpaca_paper_panel.py')
EXPECTED={
 'aion_alpaca_paper_algo.py':('MAX_COST = Decimal(\'300\')','MAX_COST = Decimal(\'2500\')'),
 'aion_kairos_server.py':('BLOCKED_OPPOSING_INTENT','REVERSAL_INTENT_LOGGED'),
 'aion_execution_journal.py':('def summarize_fills(','0 if x["action"]=="BUY_TO_OPEN" else 1'),
 'aion_alpaca_paper_panel.py':('Alpaca PAPER','max_entry_premium_usd'),
}


def existing_armed() -> bool:
    state=ROOT/'aion_alpaca_paper_algo.sqlite3'
    if not state.exists():return False
    try:
        with sqlite3.connect(f'file:{state.as_posix()}?mode=ro',uri=True,timeout=2) as db:
            row=db.execute("SELECT value FROM cfg WHERE name='armed'").fetchone()
        return row is not None and row[0]=='true'
    except sqlite3.OperationalError as ex:
        raise RuntimeError('Could not verify previous PAPER bot arm state; no changes made') from ex


def install() -> None:
    if existing_armed():
        raise RuntimeError('BOT IS STILL ARMED. Run old aion_alpaca_paper_algo.py disarm and stop the worker first.')
    replacements={}
    unchanged=True
    for filename in FILES:
        path=ROOT/filename
        replacement=SOURCE/filename
        if not path.is_file() or not replacement.is_file():
            raise RuntimeError(f'{filename} missing; extract the ZIP into the existing AION-Execution folder')
        previous=path.read_text(encoding='utf-8')
        code=replacement.read_text(encoding='utf-8')
        if EXPECTED[filename][0] not in previous and EXPECTED[filename][1] not in previous:
            raise RuntimeError(f'{filename} differs from the supported GitHub release; refusing to overwrite local edits')
        ast.parse(code,filename=filename)
        if filename=='aion_alpaca_paper_algo.py' and 'https://paper-api.alpaca.markets' not in code:
            raise RuntimeError('Paper-only API host not found; refusing installation')
        if previous!=code:
            unchanged=False
        replacements[path]=code
    if unchanged:
        print('ALREADY INSTALLED: KAIROS V5 $2,500 Alpaca PAPER execution upgrade.')
        return
    backup=ROOT/'aion-kairos-2500-backups'/datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    backup.mkdir(parents=True,exist_ok=False)
    for path in replacements:shutil.copy2(path,backup/path.name)
    try:
        for path,body in replacements.items():
            tmp=path.with_name(path.name+'.aion2500-tmp')
            tmp.write_text(body,encoding='utf-8')
            os.replace(tmp,path)
    except Exception:
        for path in replacements:
            shutil.copy2(backup/path.name,path)
        raise
    # Documentation and tests are copied, never overwrite actual account/history DB.
    docs=ROOT/'README_AION_KAIROS_V5_2500_PREMIUM.md'
    if (SOURCE/'README_AION_KAIROS_V5_2500_PREMIUM.md').exists():
        shutil.copy2(SOURCE/'README_AION_KAIROS_V5_2500_PREMIUM.md',docs)
    test=SOURCE/'tests'/'test_kairos_v5_paper_budget.py'
    if test.exists():
        d=ROOT/'tests';d.mkdir(exist_ok=True)
        shutil.copy2(test,d/test.name)
    print('SUCCESS: KAIROS V5 $2,500 total-premium PAPER sizing and signal-driven exits installed.')
    print('No market orders, brokerage orders, credential changes or data deletion performed.')
    print('Backups:',backup)
    print('Keep DISARMED. Restart port 8001 and port 8501, verify account, quote chain and chart.')

if __name__=='__main__':
    try:install()
    except Exception as e:
        print('NO UPGRADE APPLIED:',str(e),file=sys.stderr)
        raise SystemExit(2)