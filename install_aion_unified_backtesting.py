"""Backup-first, idempotent AION Quant Labs integrated backtesting upgrade.

Preserves the existing mobile dashboard, signals, execution journal, .env, and logo.
Copies user's original v0.2 research code as a package and mounts it on /lab.
No SnapTrade/broker order submission functions are imported.
"""
from __future__ import annotations

import ast
import re
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
APP = ROOT / 'aion_live_dashboard.py'
HTML = ROOT / 'static' / 'index.html'
BUNDLE = ROOT / 'backtest_bundle'
PARTS = ROOT / 'unified_parts'
ENGINE = ROOT / 'aion_backtest_engine'

MOUNT = '''\n# AION original Quant Terminal v0.2 research engine (same process, /lab prefix).
# It is intentionally isolated from the live order execution adapter.
from aion_backtest_engine.server import app as aion_embedded_backtest_app
app.mount("/lab", aion_embedded_backtest_app, name="aion_integrated_backtests")
'''


def rewrite(app: str, html: str) -> tuple[str, str]:
    required = ('id="overview"', 'id="signals"', 'id="executions"', 'id="system"')
    if not all(x in html for x in required):
        raise RuntimeError('Dashboard does not match the AION mobile monitor; refusing to alter it.')
    if 'id="backtests" class="section"' not in html:
        raise RuntimeError('Research Suite must be installed first (missing Backtesting tab).')
    if 'from aion_research import router as research_router' not in app:
        raise RuntimeError('Research Suite must be installed first (missing research routes).')
    if 'app.mount("/lab",' not in app:
        where = 'app.include_router(research_router)'
        if app.count(where) != 1:
            raise RuntimeError('Expected research router anchor missing. No changes made.')
        app = app.replace(where, where + '\n' + MOUNT, 1)

    panel = (PARTS/'backtests_panel.html').read_text(encoding='utf-8').rstrip()
    # The research update inserts the Backtesting section immediately before System.
    region = re.compile(r'    <div id="backtests" class="section">.*?(?=\n    <div id="system" class="section">)', re.S)
    if len(region.findall(html)) != 1:
        raise RuntimeError('Backtest section boundary changed; refusing partial replacement.')
    html = region.sub(lambda _: panel, html, count=1)

    css = (PARTS/'backtests_styles.css').read_text(encoding='utf-8')
    if '/* Integrated AION original backtesting engine' not in html:
        if html.count('  </style>') != 1:
            raise RuntimeError('Unexpected dashboard CSS structure.')
        html = html.replace('  </style>', css + '\n  </style>', 1)

    # Previously installed research script checks the old port-8000 link.
    # Disable that legacy checker now that the engine is mounted locally.
    start = html.find(' async function checkTerminal(){')
    if start >= 0:
        end = html.find(" $('scan-go').addEventListener", start)
        if end < 0:
            raise RuntimeError('Could not safely disable old terminal-link checker.')
        html = html[:start] + ' async function checkTerminal(){return;}\n' + html[end:]
    html = html.replace("$('terminal-recheck').addEventListener('click',checkTerminal);", '')
    html = html.replace("if(section==='backtests')checkTerminal();", "if(section==='backtests')integratedLab.init();")

    js = (PARTS/'backtests_script.js').read_text(encoding='utf-8')
    if '// AION Unified Lab:' not in html:
        anchor='refresh();setInterval(refresh,8000);'
        if html.count(anchor) != 1:
            raise RuntimeError('Cannot locate AION refresh anchor; no changes made.')
        html = html.replace(anchor, js + '\n' + anchor, 1)

    html = html.replace('● READ-ONLY MONITOR', '● PRIVATE MONITOR + RESEARCH')
    html = html.replace('No orders or broker credentials are accessible through this website.',
                        'Backtests can be run and imported here. No brokerage order controls or credentials are exposed.')
    for id in ('lab-run-stock','lab-run-options','lab-import-trades','lab-run-select','lab-signal-set','lab-equity'):
        if f'id="{id}"' not in html:
            raise RuntimeError(f'Missing integrated control {id}; no changes made.')
    if html.count('</script>') != 1:raise RuntimeError('Unrecognized dashboard script structure.')
    ast.parse(app)
    return app,html


def verify_files():
    for p in [APP,HTML,BUNDLE/'__init__.py',BUNDLE/'server.py',BUNDLE/'market_v2.py',
              BUNDLE/'engine_v2.py',BUNDLE/'options_v2.py',BUNDLE/'static'/'index.html',
              BUNDLE/'static'/'app.js',BUNDLE/'static'/'style.css',
              PARTS/'backtests_panel.html',PARTS/'backtests_styles.css',PARTS/'backtests_script.js']:
        if not p.is_file():raise RuntimeError(f'Missing package component: {p.relative_to(ROOT)}')
    for f in (BUNDLE/'server.py',BUNDLE/'market_v2.py',BUNDLE/'engine_v2.py',BUNDLE/'options_v2.py'):
        ast.parse(f.read_text(encoding='utf-8'))


def migrate_old_db():
    """Copy an old research DB once, preserving existing integrated DB if present."""
    destination=ENGINE/'data'/'aion.sqlite3'
    if destination.exists():return 'Existing integrated backtest database preserved.'
    locations=[Path.home()/'Downloads'/'AION_Quant_Terminal_v0_2_Stock_0DTE'/'aion-quant-terminal'/'data'/'aion.sqlite3',
               Path.home()/'aion-quant-terminal'/'data'/'aion.sqlite3']
    for old in locations:
        if old.is_file():
            destination.parent.mkdir(parents=True, exist_ok=True)
            try:
                with sqlite3.connect(str(old), timeout=15) as src, sqlite3.connect(str(destination)) as dst:
                    src.backup(dst)
                return f'Existing backtests copied safely from {old}. Original kept unchanged.'
            except Exception as exc:
                destination.unlink(missing_ok=True)
                return f'Previous DB was detected but could not be copied ({type(exc).__name__}). Original data unchanged.'
    return 'Created a new integrated research database. No old database was found to migrate.'


def main():
    verify_files()
    old_app=APP.read_text(encoding='utf-8')
    old_html=HTML.read_text(encoding='utf-8')
    new_app,new_html=rewrite(old_app,old_html)
    source_changed=not (ENGINE/'server.py').exists()
    if old_app==new_app and old_html==new_html and not source_changed:
        print('Already installed: the original AION v0.2 backtesting engine is integrated at /lab.')
        return
    backup=ROOT/'aion-unified-backups'/datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    backup.mkdir(parents=True,exist_ok=False)
    shutil.copy2(APP,backup/'aion_live_dashboard.py')
    shutil.copy2(HTML,backup/'index.html')
    ENGINE.mkdir(parents=True,exist_ok=True)
    for p in BUNDLE.rglob('*'):
        if p.is_file():
            out=ENGINE/p.relative_to(BUNDLE)
            out.parent.mkdir(parents=True,exist_ok=True)
            # Never overwrite the embedded SQLite DB, user settings, or provider keys.
            shutil.copy2(p,out)
    old_data=migrate_old_db()
    APP.write_text(new_app,encoding='utf-8')
    HTML.write_text(new_html,encoding='utf-8')
    print('SUCCESS: Original AION Quant Terminal v0.2 integrated into your mobile dashboard.')
    print('Single site: http://127.0.0.1:8501 ; backtests run at /lab/api/...')
    print('KAIROS stock-port runs, genuine 0DTE quote replay, CSV imports and saved-run history enabled.')
    print(old_data)
    print('Backups:', backup)
    print('Exact AION logo, all signals, research tabs and fills remain unchanged.')
    print('Restart only the dashboard service on port 8501. No broker order code added.')

if __name__=='__main__':main()
