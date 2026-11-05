"""Conservative AION Alpaca paper strategy installer: backup-first and idempotent.
Does not alter TradingView gateway, signal engine, .env or broker orders.
"""
from __future__ import annotations
import ast
import shutil
from pathlib import Path
from datetime import datetime

ROOT=Path(__file__).resolve().parent
APP=ROOT/'aion_live_dashboard.py'
HTML=ROOT/'static'/'index.html'
TAG='# AION_ALPACA_PAPER_ALGO_PANEL_V1'
BLOCK='''\n# AION_ALPACA_PAPER_ALGO_PANEL_V1 — read-only chart/status only, no trade endpoints.
from aion_alpaca_paper_panel import app as aion_alpaca_paper_panel
app.mount("/alpaca-paper", aion_alpaca_paper_panel, name="aion_alpaca_paper_panel")
'''
PANEL='''
      <!-- AION_ALPACA_PAPER_ALGO_PANEL_V1 — paper orders are NOT submitted by this UI -->
      <div style="margin:18px 0;padding:15px;border:1px solid #5d6a86;border-radius:12px;background:#101e34">
        <h3 style="color:#f1d17a">Alpaca PAPER — SPY 0DTE options chain &amp; chart</h3>
        <p style="color:#c2ccdb">Read-only account and quote visualization. KAIROS PAPER orders are controlled separately by the local command-line worker; real brokerage accounts are excluded.</p>
        <iframe title="Alpaca Paper options market data and order monitor" src="/alpaca-paper/"
          loading="lazy" style="width:100%;height:670px;background:#0b1427;border:1px solid #4a5670;border-radius:9px"></iframe>
      </div>
'''

def main():
    files=[APP,HTML,ROOT/'aion_alpaca_paper_algo.py',ROOT/'aion_alpaca_paper_panel.py',
           ROOT/'aion_execution_journal.py',ROOT/'aion_kairos_server.py',ROOT/'aion_tv_gateway.py']
    for f in files:
        if not f.is_file():raise SystemExit('Missing '+str(f)+'; extract package into C:\\Users\\sathw\\AION-Execution')
    app=APP.read_text(encoding='utf-8');page=HTML.read_text(encoding='utf-8')
    if 'app.mount("/lab",' not in app or 'id="backtests" class="section"' not in page:
        raise SystemExit('Expected AION Unified Dashboard missing; refusing modification.')
    if TAG in app and '<!-- AION_ALPACA_PAPER_ALGO_PANEL_V1' in page:
        print('Already installed. Dashboard paper panel available at /alpaca-paper/.')
        return
    if TAG in app or '<!-- AION_ALPACA_PAPER_ALGO_PANEL_V1' in page:
        raise SystemExit('Partial/conflicting panel patch found. Inspect backup; no files modified.')
    a='app.mount("/lab",'
    if app.count(a)!=1:raise SystemExit('Unexpected dashboard app mount; no changes.')
    app=app.replace(a,BLOCK+'\n'+a,1)
    b='<div id="backtests" class="section">'
    if page.count(b)!=1:raise SystemExit('Unexpected dashboard Backtesting markup; no changes.')
    page=page.replace(b,b+PANEL,1)
    ast.parse(app)
    backup=ROOT/'aion-alpaca-paper-backups'/datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    backup.mkdir(parents=True,exist_ok=False)
    shutil.copy2(APP,backup/'aion_live_dashboard.py')
    shutil.copy2(HTML,backup/'index.html')
    APP.write_text(app,encoding='utf-8');HTML.write_text(page,encoding='utf-8')
    print('SUCCESS: Read-only Alpaca Paper chart integrated into AION Backtesting tab.')
    print('TradingView webhook, signal engine, .env, and Webull untouched.')
    print('Alpaca order worker remains DISARMED until you explicitly arm it.')
    print('Dashboard backup:',backup)
    print('Restart ONLY dashboard 8501 after installing. Worker is started separately.')

if __name__=='__main__':main()
