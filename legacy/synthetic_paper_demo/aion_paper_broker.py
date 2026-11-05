"""Explicitly synthetic, read-only-broker paper-demo adapter for AION KAIROS V5.

ONLY accepts paper_demo_id tagged test events from the SIMULATION server.
Writes synthetic PAPER fills into the existing dashboard journal (SQLite).
NO trading APIs, no credentials, no broker orders, no market quote lookups.
The displayed prices are examples, not observed SPY option quotes.
"""
from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from aion_execution_journal import DEFAULT_DB, ensure_db

DEMO_ID = re.compile(r'^AION_DEMO_[0-9a-f]{12}$')
NY = ZoneInfo('America/New_York')
SOURCE = 'SYNTHETIC_PAPER_DEMO_NO_MARKET_QUOTES'
ENTRY_PREMIUM = 1.25   # synthetic option ask, USD/share, NOT a live quote
EXIT_PREMIUM = 1.10    # synthetic option bid, USD/share, NOT a live quote
SIDE_FEE_USD = 0.65


def _tables(con: sqlite3.Connection) -> None:
    con.execute('''CREATE TABLE IF NOT EXISTS paper_demo_events (
      event_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, signal TEXT NOT NULL,
      result TEXT NOT NULL, processed_at TEXT NOT NULL
    )''')
    con.execute('''CREATE TABLE IF NOT EXISTS paper_demo_positions (
      run_id TEXT PRIMARY KEY, option_symbol TEXT NOT NULL,
      side TEXT NOT NULL, state TEXT NOT NULL CHECK(state IN ('OPEN','CLOSED')),
      entry_at TEXT NOT NULL, exit_at TEXT NOT NULL DEFAULT ''
    )''')


def _option_symbol(side: str, when: datetime, spot: float) -> str:
    if side not in ('BUY_CALL', 'BUY_PUT'):
        raise ValueError('Unsupported option direction')
    if not isinstance(spot, (int, float)) or not 100 <= float(spot) <= 2000:
        raise ValueError('Synthetic test requires a supplied illustrative SPY price in [100, 2000]')
    expiry = when.astimezone(NY).strftime('%y%m%d')
    strike = round(float(spot) * 2) / 2.0
    cp = 'C' if side == 'BUY_CALL' else 'P'
    return f'{"SPY":<6}{expiry}{cp}{int(round(strike * 1000)):08d}'


def process_demo(*, run_id: str, event_id: str, signal: str,
                 emitted_at: datetime, underlying_price: float | None,
                 journal_db: Path = DEFAULT_DB) -> dict:
    """Atomically log demo event + synthetic fill + demo position in journal DB.

    This runs ONLY for explicitly tagged demo messages.  Does not read or mutate
    the ordinary signal receiver's events/state tables or any brokerage account.
    """
    if not DEMO_ID.fullmatch(run_id):
        raise ValueError('Invalid paper-demo identifier')
    if not re.fullmatch(r'tvv5-[0-9a-f]{64}', event_id):
        raise ValueError('Only validated gateway-derived demo event IDs are accepted')
    if signal not in ('BUY_CALL', 'BUY_PUT', 'EXIT'):
        raise ValueError('Invalid KAIROS demo signal')
    if emitted_at.utcoffset() is None:
        raise ValueError('Demo signal must be timezone-aware')
    age = (datetime.now(timezone.utc) - emitted_at.astimezone(timezone.utc)).total_seconds()
    if age < -15 or age > 180:
        raise ValueError('Synthetic alert is stale or future dated')

    ensure_db(journal_db)
    now = datetime.now(timezone.utc).isoformat()
    con = sqlite3.connect(str(journal_db), timeout=8, isolation_level=None)
    con.row_factory = sqlite3.Row
    try:
        con.execute('BEGIN IMMEDIATE')
        _tables(con)
        seen = con.execute('SELECT result FROM paper_demo_events WHERE event_id=?', (event_id,)).fetchone()
        if seen is not None:
            con.execute('COMMIT')
            return {'result': 'DUPLICATE', 'original_result': seen['result'], 'paper_fill_created': False,
                    'broker_order_sent': False, 'pricing': 'SYNTHETIC', 'run_id': run_id}
        position = con.execute('SELECT * FROM paper_demo_positions WHERE run_id=?', (run_id,)).fetchone()
        action = None
        option_symbol = None
        if signal in ('BUY_CALL', 'BUY_PUT'):
            if position is not None:
                outcome = 'IGNORED_ALREADY_OPEN_OR_CLOSED'
            elif underlying_price is None:
                outcome = 'NO_SYNTHETIC_SPOT_SUPPLIED'
            else:
                option_symbol = _option_symbol(signal, emitted_at, underlying_price)
                action = 'BUY_TO_OPEN'
                outcome = 'SYNTHETIC_PAPER_OPENED'
                con.execute('''INSERT INTO paper_demo_positions(run_id, option_symbol, side, state, entry_at)
                               VALUES (?, ?, ?, 'OPEN', ?)''', (run_id, option_symbol, signal, now))
        else:
            if position is None:
                outcome = 'IGNORED_NO_PAPER_POSITION'
            elif position['state'] != 'OPEN':
                outcome = 'IGNORED_ALREADY_CLOSED'
            else:
                option_symbol = position['option_symbol']
                action = 'SELL_TO_CLOSE'
                outcome = 'SYNTHETIC_PAPER_CLOSED'
                con.execute("UPDATE paper_demo_positions SET state='CLOSED', exit_at=? WHERE run_id=?", (now, run_id))

        if action is not None:
            fill_id = f'{run_id}-{action}'
            con.execute('''INSERT INTO option_fills
               (fill_id, filled_at, strategy, symbol, option_symbol, mode, action,
                contracts, fill_price, fees_usd, source, broker_order_id)
               VALUES (?, ?, 'KAIROS_V5', 'SPY', ?, 'PAPER', ?, 1, ?, ?, ?, '')''',
               (fill_id, now, option_symbol, action,
                ENTRY_PREMIUM if action == 'BUY_TO_OPEN' else EXIT_PREMIUM,
                SIDE_FEE_USD, SOURCE))
        con.execute('''INSERT INTO paper_demo_events(event_id,run_id,signal,result,processed_at)
                       VALUES (?,?,?,?,?)''', (event_id, run_id, signal, outcome, now))
        con.execute('COMMIT')
        return {'result': outcome, 'paper_fill_created': bool(action), 'broker_order_sent': False,
                'pricing': 'SYNTHETIC_NOT_MARKET', 'run_id': run_id,
                'contract': option_symbol, 'mode': 'PAPER', 'fill_source': SOURCE}
    except Exception:
        try:
            con.execute('ROLLBACK')
        except sqlite3.Error:
            pass
        raise
    finally:
        con.close()
