"""One-command synthetic alert -> PAPER entry -> duplicate -> PAPER exit test.

Runs both original AION gateway and signal app IN PROCESS using FastAPI's
TestClient and a local-only transport. No external URL, network, brokerage,
credential disclosure, or live market price is involved. Test messages bypass
market hours ONLY via the explicit paper_demo_id path. Ordinary KAIROS state
and events are never touched. Paper fills appear in dashboard's REAL journal.
"""
from __future__ import annotations

import io
import os
import sqlite3
import uuid
from datetime import datetime, timezone

from fastapi.testclient import TestClient

import aion_tv_gateway as gateway
import aion_kairos_server as simulator
from aion_execution_journal import DEFAULT_DB, summarize_fills


def normal_snapshot():
    with sqlite3.connect(simulator.DB, timeout=5) as c:
        return (c.execute('SELECT intent_state FROM state WHERE id=1').fetchone()[0],
                c.execute('SELECT COUNT(*) FROM events').fetchone()[0])


def main():
    if os.getenv('AION_MODE', 'SIMULATION').upper() != 'SIMULATION':
        raise SystemExit('Refusing test. AION_MODE must be SIMULATION.')
    if len(gateway.WEBHOOK_SECRET) < 32:
        raise SystemExit('TradingView webhook test secret missing; run configure_tv_secret.py first.')
    run_id = 'AION_DEMO_' + uuid.uuid4().hex[:12]
    entered = datetime.now(timezone.utc).isoformat()
    before = normal_snapshot()
    simclient = TestClient(simulator.app)

    def local_forward(request, timeout=1.7):
        """In-memory route instead of network to ensure zero public exposure."""
        if request.full_url != 'http://127.0.0.1:8001/webhook':
            raise RuntimeError('Test blocked unexpected outgoing URL')
        response = simclient.post('/webhook', content=request.data,
                                  headers={'Content-Type': 'application/json'})
        if response.status_code >= 400:
            from urllib.error import HTTPError
            raise HTTPError(request.full_url, response.status_code, 'Simulation receiver declined',
                            None, io.BytesIO(response.content))
        return io.BytesIO(response.content)

    gateway.urllib.request.urlopen = local_forward
    client = TestClient(gateway.app)

    def send(action, order_id, order_action):
        payload = {
            'webhook_secret': gateway.WEBHOOK_SECRET,
            'strategy': 'KAIROS_V5', 'ticker': 'SPY', 'timeframe': '3',
            'order_id': order_id, 'order_action': order_action,
            'order_message': action, 'timenow': entered,
            'underlying_price': 700.0,  # ILLUSTRATIVE, NOT a current SPY quote
        }
        response = client.post('/tv-webhook', json=payload)
        if response.status_code != 200:
            raise RuntimeError(f'Local gateway rejected demo: HTTP {response.status_code} ({response.text[:200]})')
        return response.json()['paper_demo']

    print('AION synthetic KAIROS V5 paper trade — NO brokerage calls')
    print('Demo option spot $700.00 is fictitious, NOT current market data.')
    entry = send('KAIROS LC V5 LONG', run_id + '_ENTRY', 'buy')
    print('1. Entry:', entry['result'], '·', entry.get('contract'))
    if entry['result'] != 'SYNTHETIC_PAPER_OPENED':
        raise RuntimeError('Entry was not accepted')
    repeat = send('KAIROS LC V5 LONG', run_id + '_ENTRY', 'buy')
    print('2. Duplicate:', repeat['result'])
    if repeat['result'] != 'DUPLICATE':
        raise RuntimeError('Duplicate protection failed')
    exit_event = send('KAIROS LC V5 LONG EXIT', run_id + '_EXIT', 'sell')
    print('3. Exit:', exit_event['result'])
    if exit_event['result'] != 'SYNTHETIC_PAPER_CLOSED':
        raise RuntimeError('Exit was not accepted')

    # Query this run directly; existing user journals may contain >500 fills.
    with sqlite3.connect(DEFAULT_DB, timeout=5) as conn:
        conn.row_factory = sqlite3.Row
        matches = [dict(row) for row in conn.execute(
            'SELECT * FROM option_fills WHERE fill_id LIKE ? ORDER BY filled_at, fill_id',
            (run_id + '-%',))]
    if len(matches) != 2:
        raise RuntimeError(f'Expected 2 journal fills; found {len(matches)}')
    result = summarize_fills(matches)
    if result['open_contracts']['PAPER'] != 0 or result['unmatched_exit_contracts']:
        raise RuntimeError('Paper position did not close cleanly')
    if normal_snapshot() != before:
        raise RuntimeError('Normal live-signal state was modified by the demo!')
    print('4. Dashboard journal:', len(matches), 'synthetic fills; PAPER contracts open: 0')
    print('5. DEMO realized P&L: ${:.2f} (fictitious prices; includes $0.65 per side)'.format(
        result['realized_pnl_usd']['PAPER']))
    print('PASS: Normal KAIROS signal state/events unchanged. No SnapTrade orders sent.')
    print('Open AION dashboard > Options Executions / Fills; refresh after 8 seconds.')
    print('DEMO RUN:',run_id)

if __name__ == '__main__':
    main()
