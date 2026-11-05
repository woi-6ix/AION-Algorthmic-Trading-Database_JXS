"""AION / Alpaca Paper — chart-only fix for intraday historical options bars.

No order actions, credential changes, or account/arm state changes.
"""
from __future__ import annotations

import ast
import os
import shutil
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent

TARGETS = {
    'aion_alpaca_paper_algo.py': [
        (
            '    def bars(self, symbol, start, end):\n',
            '    def bars(self, symbol, start, end=None):\n',
        ),
        (
            "            q = {'symbols':symbol,'timeframe':'3Min','start':start,'end':end,'feed':'indicative',\n"
            "                 'limit':1000,'sort':'asc'}\n"
            "            if token: q['page_token'] = token\n",
            "            q = {'symbols':symbol,'timeframe':'3Min','start':start,'feed':'indicative',\n"
            "                 'limit':1000,'sort':'asc'}\n"
            "            # For today's contract, omit end so Alpaca caps results to the\n"
            "            # latest history available under the market-data subscription.\n"
            "            if end is not None: q['end'] = end\n"
            "            if token: q['page_token'] = token\n",
        ),
        (
            '    bars=api.bars(symbol,start,end)\n',
            '    bars=api.bars(symbol,start,end if expiry<now_utc().astimezone(NY).date() else None)\n',
        ),
    ],
    'aion_alpaca_paper_panel.py': [
        (
            '        raw=api.bars(symbol,start,end)\n',
            '        raw=api.bars(symbol,start,end if exp<today else None)\n',
        ),
    ],
}


def install():
    outputs = {}
    already = True
    for name, changes in TARGETS.items():
        src = ROOT / name
        if not src.is_file():
            raise RuntimeError(f'{name} not found; extract patch into existing AION-Execution directory')
        current = src.read_text(encoding='utf-8')
        updated = current
        for before, after in changes:
            if before in updated:
                if updated.count(before) != 1:
                    raise RuntimeError(f'{name}: expected exactly one matching code location')
                updated = updated.replace(before, after, 1)
                already = False
            elif after not in updated:
                raise RuntimeError(f'{name}: code differs from supported AION version; no files changed')
        ast.parse(updated, filename=name)
        outputs[src] = updated
    if already:
        print('ALREADY INSTALLED: options chart request fix.')
        return
    stamp = datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    backup = ROOT / 'aion-chart-fix-backups' / stamp
    backup.mkdir(parents=True, exist_ok=False)
    for src in outputs:
        shutil.copy2(src, backup / src.name)
    try:
        for src, body in outputs.items():
            tmp = src.with_suffix(src.suffix + '.chartfix-tmp')
            tmp.write_text(body, encoding='utf-8')
            os.replace(tmp, src)
    except Exception:
        for src in outputs:
            shutil.copy2(backup / src.name, src)
        raise
    print('SUCCESS: Options historical chart requests now omit future end times on same-day contracts.')
    print('Read-only chart fix; paper order routing, arm state, credentials unchanged.')
    print('Backups:', backup)
    print('Restart ONLY the dashboard (8501) to load the updated options chart endpoint.')


if __name__ == '__main__':
    try:
        install()
    except Exception as e:
        print('NO FIX APPLIED:', str(e))
        raise SystemExit(2)
