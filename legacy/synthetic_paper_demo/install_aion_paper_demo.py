"""Backup-first and fail-closed updater for AION synthetic paper-demo pipeline.

NO live trading code is added. AION 8001/8002 remain SIMULATION ONLY.
"""
from __future__ import annotations

import ast
import shutil
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
GW = ROOT / 'aion_tv_gateway.py'
SIM = ROOT / 'aion_kairos_server.py'
PAGE = ROOT / 'static' / 'index.html'


def patch_gateway(text: str) -> str:
    if 'AION PAPER DEMO GATEWAY PATCH v1' in text:
        return text
    changes = [
        ("    timenow: str = Field(min_length=1, max_length=45)\n",
         "    timenow: str = Field(min_length=1, max_length=45)\n"
         "    underlying_price: float | None = Field(default=None, gt=0)\n"),
        ("    event_id = 'tvv5-' + hashlib.sha256(unique_source.encode('utf-8')).hexdigest()\n",
         "    event_id = 'tvv5-' + hashlib.sha256(unique_source.encode('utf-8')).hexdigest()\n"
         "    # AION PAPER DEMO GATEWAY PATCH v1: uniquely tagged test alerts only.\n"
         "    demo_match = re.fullmatch(r'(AION_DEMO_[0-9a-f]{12})_(ENTRY|EXIT)', obj.order_id)\n"
         "    demo_id = demo_match.group(1) if demo_match else None\n"
         "    if demo_match and ((demo_match.group(2) == 'ENTRY') != (signal in ('BUY_CALL', 'BUY_PUT'))):\n"
         "        raise HTTPException(422, 'Demo order_id phase does not match KAIROS alert action')\n"),
        ("        'emitted_at': d.astimezone(timezone.utc).isoformat(),\n",
         "        'emitted_at': d.astimezone(timezone.utc).isoformat(),\n"
         "        **({'underlying_price': obj.underlying_price} if obj.underlying_price is not None else {}),\n"
         "        **({'paper_demo_id': demo_id} if demo_id else {}),\n"),
        ("        'broker_order_sent': False,\n    }\n",
         "        'broker_order_sent': False,\n"
         "        **({'paper_demo': upstream} if demo_id else {}),\n"
         "    }\n"),
    ]
    for before, after in changes:
        if text.count(before) != 1:
            raise RuntimeError('Gateway source changed; patch aborted safely at: ' + before[:80])
        text = text.replace(before, after, 1)
    ast.parse(text)
    return text


def patch_simulator(text: str) -> str:
    if 'AION PAPER DEMO SIMULATOR PATCH v1' in text:
        return text
    changes = [
        ('    underlying_price: float | None = Field(default=None, gt=0)\n',
         '    underlying_price: float | None = Field(default=None, gt=0)\n'
         "    paper_demo_id: str | None = Field(default=None, pattern=r'^AION_DEMO_[0-9a-f]{12}$')\n"),
        ('    check_time(signal)\n\n    now = datetime.now(timezone.utc).isoformat()\n',
         "    # AION PAPER DEMO SIMULATOR PATCH v1: isolate demo events from normal signal state.\n"
         "    if signal.paper_demo_id is not None:\n"
         "        from aion_paper_broker import process_demo\n"
         "        # Freshness validated by process_demo; NY session bypass applies ONLY\n"
         "        # to synthetic, broker-disabled test events.\n"
         "        try:\n"
         "            return process_demo(run_id=signal.paper_demo_id, event_id=signal.event_id,\n"
         "                signal=signal.signal, emitted_at=signal.emitted_at,\n"
         "                underlying_price=signal.underlying_price)\n"
         "        except ValueError as exc:\n"
         "            raise HTTPException(422, str(exc)) from None\n"
         "    check_time(signal)\n\n    now = datetime.now(timezone.utc).isoformat()\n"),
    ]
    for before, after in changes:
        if text.count(before) != 1:
            raise RuntimeError('Simulator source changed; patch aborted safely at: ' + before[:80])
        text = text.replace(before, after, 1)
    ast.parse(text)
    return text


def patch_html(text: str) -> str:
    if '<th>Fees</th><th>Source</th>' in text:
        return text
    a = '<th>Fees</th>'
    b = '${money(f.fees_usd||0)}</td></tr>`));'
    if text.count(a) != 1 or text.count(b) != 1:
        raise RuntimeError('Dashboard execution ledger markup changed; refusing partial patch.')
    text = text.replace(a, '<th>Fees</th><th>Source</th>', 1)
    text = text.replace(b, '${money(f.fees_usd||0)}</td><td>${escape(f.source||"—")}</td></tr>`));', 1)
    text = text.replace('Only confirmed paper or brokerage fills; never inferred from alerts.',
        'Paper demo fills are synthetic examples; Source identifies demo versus broker data.')
    text = text.replace('Paper realized P&L', 'Paper P&L (simulated)')
    text = text.replace('<div class="metlabel">Realized P&amp;L</div>',
                        '<div class="metlabel">Journal P&amp;L (incl. demo)</div>')
    text = text.replace('<div class="metlabel">Realized P&L</div>',
                        '<div class="metlabel">Journal P&L (incl. demo)</div>')
    text = text.replace("'Matched fill P&L before FX'",
                        "'Includes synthetic demos; NOT live strategy P&L'")
    text = text.replace('Signal logs show intent only. Options fills and P&L require the future verified execution adapter.',
        'Synthetic paper-demo fills are NOT live quotes or broker executions. Broker fills require confirmed orders.')
    return text


def main():
    for path in (GW, SIM, PAGE, ROOT/'aion_execution_journal.py',ROOT/'aion_paper_broker.py',ROOT/'aion_paper_cycle_test.py'):
        if not path.is_file():
            raise SystemExit(f'Missing {path}; install the AION files in the AION-Execution folder.')
    if 'SIMULATION ONLY' not in GW.read_text(encoding='utf-8') or 'LIVE ORDER ROUTING IS NOT IMPLEMENTED' not in SIM.read_text(encoding='utf-8'):
        raise SystemExit('Source is no longer the approved simulation-only gateway/simulator; refusing patch.')
    before = {p: p.read_text(encoding='utf-8') for p in (GW,SIM,PAGE)}
    try:
        after = {GW:patch_gateway(before[GW]), SIM:patch_simulator(before[SIM]), PAGE:patch_html(before[PAGE])}
    except Exception as exc:
        raise SystemExit(f'No source changes written: {exc}')
    changed = [p for p in before if before[p] != after[p]]
    if not changed:
        print('Already installed. Paper-demo bridge and Source column found.')
        return
    folder=ROOT/'aion-paper-backups'/datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    folder.mkdir(parents=True)
    for p in (GW,SIM,PAGE):
        to=folder/p.relative_to(ROOT)
        to.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(p,to)
    for p in changed:
        p.write_text(after[p],encoding='utf-8')
    print('SUCCESS: Synthetic paper-demo webhook flow installed.')
    print('Backups:',folder)
    print('Normal KAIROS signal state preserved. SnapTrade / Webull untouched.')
    print('No broker order submission is possible through these components.')
    print('Run: .\\.venv\\Scripts\\python.exe .\\aion_paper_cycle_test.py')
    print('Then refresh the AION dashboard Options Executions tab.')
    print('Restart port 8001, 8002, and 8501 at a convenient point to load patched web services.')

if __name__=='__main__':main()
