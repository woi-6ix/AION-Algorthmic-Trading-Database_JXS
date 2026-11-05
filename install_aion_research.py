"""Idempotent, backup-first AION mobile research update (no trade code changes)."""
from __future__ import annotations
import ast
import shutil
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
APP = ROOT / 'aion_live_dashboard.py'
HTML = ROOT / 'static' / 'index.html'
PARTS = ROOT / 'research_components'
MODULE = ROOT / 'aion_research.py'


def apply_update(app: str, html: str, css: str, sections: str, js: str):
    if 'from aion_research import router as research_router' not in app:
        anchor = 'from aion_execution_journal import DEFAULT_DB, read_fills, summarize_fills'
        if app.count(anchor) != 1:
            raise ValueError('Dashboard code changed; expected import not found. No changes made.')
        app = app.replace(anchor, anchor + '\nfrom aion_research import router as research_router', 1)
    if 'app.include_router(research_router)' not in app:
        anchor = 'app = FastAPI(title="AION Live Monitor — Read Only", docs_url=None, redoc_url=None, openapi_url=None)'
        if app.count(anchor) != 1:
            raise ValueError('Dashboard app declaration changed. No changes made.')
        app = app.replace(anchor, anchor + '\napp.include_router(research_router)', 1)
    if '/* AION Research Suite */' not in html:
        if html.count('  </style>') != 1:
            raise ValueError('CSS closing tag not found. No changes made.')
        html = html.replace('  </style>', css + '\n  </style>', 1)
    if 'id="screener" class="section"' not in html:
        anchor = '    <div id="system" class="section">'
        if html.count(anchor)!=1:
            raise ValueError('System panel not found. No changes made.')
        html=html.replace(anchor, sections+'\n'+anchor,1)
    desktop_nav = '<button data-section="system"><span class="navsvg">⚙</span> System Health</button>'
    new_desktop = ('<button data-section="screener" class="research-nav"><span class="navsvg">⌕</span> Stock Screener</button>\n'
                   '      <button data-section="sentiment" class="research-nav"><span class="navsvg">☷</span> News & Sentiment</button>\n'
                   '      <button data-section="regimes" class="research-nav"><span class="navsvg">◈</span> Market Regimes</button>\n'
                   '      <button data-section="backtests" class="research-nav"><span class="navsvg">▤</span> Backtesting</button>\n      ')
    if 'class="research-nav"' not in html:
        if html.count(desktop_nav) != 1:raise ValueError('Desktop sidebar target not found')
        html=html.replace(desktop_nav,new_desktop+desktop_nav,1)
    mobile_nav = '<button data-section="system"><b>⚙</b>System</button>'
    new_mobile = ('<button data-section="screener"><b>⌕</b>Scan</button>'
                  '<button data-section="sentiment"><b>☷</b>News</button>'
                  '<button data-section="regimes"><b>◈</b>Regime</button>'
                  '<button data-section="backtests"><b>▤</b>Backtests</button>')
    if '<button data-section="screener"><b>' not in html:
        if html.count(mobile_nav)!=1:raise ValueError('Mobile navigation target not found')
        html=html.replace(mobile_nav,new_mobile+mobile_nav,1)
    if '// AION Research Suite: read-only browser handlers.' not in html:
        anchor='refresh();setInterval(refresh,8000);'
        if html.count(anchor)!=1:raise ValueError('Refresh script target not found')
        html=html.replace(anchor,js+'\n'+anchor,1)
    # syntax validation for Python and essential structural sanity for HTML
    ast.parse(app)
    for required in ('id="overview"','id="signals"','id="executions"','id="system"','id="screener"','id="sentiment"','id="regimes"','id="backtests"','aion-logo.png'):
        if required not in html: raise ValueError('Required dashboard element missing: '+required)
    if html.count('</script>') != 1:raise ValueError('Unexpected script structure')
    return app,html


def main():
    for path in [APP, HTML, MODULE, PARTS/'research_styles.css',PARTS/'research_sections.html',PARTS/'research_script.js']:
        if not path.exists():
            raise SystemExit(f'Missing required file {path.name}. Extract the complete ZIP into your AION-Execution folder.')
    old_app=APP.read_text(encoding='utf-8')
    old_html=HTML.read_text(encoding='utf-8')
    new_app,new_html=apply_update(old_app, old_html,
                                  (PARTS/'research_styles.css').read_text(encoding='utf-8'),
                                  (PARTS/'research_sections.html').read_text(encoding='utf-8'),
                                  (PARTS/'research_script.js').read_text(encoding='utf-8'))
    if old_app==new_app and old_html==new_html:
        print('Already installed. AION research tabs and backend routes are present.')
        return
    backup=ROOT/'aion-research-backups'/datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    backup.mkdir(parents=True,exist_ok=False)
    shutil.copy2(APP,backup/APP.name)
    shutil.copy2(HTML,backup/'index.html')
    # Save only the two edited files after all validation is complete.
    APP.write_text(new_app,encoding='utf-8')
    HTML.write_text(new_html,encoding='utf-8')
    print('SUCCESS: Stock Screener, News & Sentiment, Market Regimes, Backtesting Hub installed.')
    print('Existing KAIROS signals, fills, exact AION logo, and broker settings preserved.')
    print('Backups:', backup)
    print('Restart ONLY the dashboard service on port 8501 and refresh your phone.')
    print('Research-only: no brokerage submission/real-time options data added.')

if __name__=='__main__':main()
