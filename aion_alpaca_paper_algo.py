"""AION KAIROS V5 -> Alpaca PAPER SPY 0DTE options worker.

STRICTLY PAPER ONLY. Hardcoded paper API. No web order endpoint.
Consumes only accepted real KAIROS V5 signals from the existing local
simulator's SQLite events, never public unauthenticated JSON.

CLI: check | chain | chart | arm | disarm | run | once | sync | status
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import sqlite3
import sys
import time as time_module
import urllib.error
import urllib.parse
import urllib.request
from contextlib import contextmanager
from datetime import datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_DOWN, ROUND_UP
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / '.env')
NY = ZoneInfo('America/New_York')
TRADING = 'https://paper-api.alpaca.markets'  # DO NOT make configurable
DATA = 'https://data.alpaca.markets'          # read-only market-data host
STATE = ROOT / 'aion_alpaca_paper_algo.sqlite3'
SIGNALS = ROOT / 'aion_kairos_v5_signals.sqlite3'
KILL = ROOT / 'AION_PAPER_KILL_SWITCH.txt'
LOCK = ROOT / '.aion_alpaca_paper_worker.lock'
OCC = re.compile(r'^SPY\d{6}[CP]\d{8}$')
API_TIMEOUT = 7
MAX_COST = Decimal('2500')  # Maximum total debit for one KAIROS V5 entry, not per contract
MAX_CONTRACTS = 25  # Additional safety cap for very low priced 0DTE contracts
MAX_SPREAD = Decimal('0.40')
MAX_SPREAD_FRACTION = Decimal('0.35')
QUOTE_AGE = timedelta(seconds=120)
SIGNAL_AGE = timedelta(seconds=55)
TERMINAL = {'filled', 'canceled', 'expired', 'rejected', 'replaced'}


def now_utc():
    return datetime.now(timezone.utc)


def dt(raw):
    t = datetime.fromisoformat(str(raw).replace('Z', '+00:00'))
    if t.tzinfo is None:
        raise ValueError('timestamp has no time zone')
    return t.astimezone(timezone.utc)


def amount(value, name='value'):
    try:
        d = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise RuntimeError(f'Invalid {name}') from None
    if not d.is_finite():
        raise RuntimeError(f'Nonfinite {name}')
    return d


def config_guard():
    if os.environ.get('AION_MODE', 'SIMULATION').strip().upper() != 'SIMULATION':
        raise RuntimeError('Existing AION_MODE MUST remain SIMULATION; paper bot refuses other modes')
    if not os.getenv('ALPACA_PAPER_API_KEY') or not os.getenv('ALPACA_PAPER_SECRET_KEY'):
        raise RuntimeError('Set only ALPACA_PAPER_API_KEY and ALPACA_PAPER_SECRET_KEY in local .env')


class AlpacaPaper:
    """Requests can only reach fixed Alpaca hosts; paper base for all order operations."""
    def __init__(self):
        config_guard()
        self.headers = {
            'APCA-API-KEY-ID': os.environ['ALPACA_PAPER_API_KEY'],
            'APCA-API-SECRET-KEY': os.environ['ALPACA_PAPER_SECRET_KEY'],
            'Accept': 'application/json',
        }

    def call(self, method, route, *, data=False, params=None, body=None):
        if route.startswith('http') or not route.startswith('/') or '..' in route:
            raise RuntimeError('Invalid API path')
        if method not in ('GET', 'POST', 'DELETE'):
            raise RuntimeError('Invalid method')
        if data and method != 'GET':
            raise RuntimeError('Market-data host is read-only')
        url = (DATA if data else TRADING) + route
        if params:
            url += '?' + urllib.parse.urlencode(params)
        payload = json.dumps(body).encode() if body is not None else None
        headers = dict(self.headers)
        if body is not None:
            headers['Content-Type'] = 'application/json'
        req = urllib.request.Request(url, data=payload, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=API_TIMEOUT) as r:
                data_raw = r.read(2_000_000)
                return json.loads(data_raw) if data_raw else {}
        except urllib.error.HTTPError as e:
            # Do not echo raw API responses, which may contain account information.
            raise RuntimeError(f'Alpaca {"DATA" if data else "PAPER"} HTTP {e.code} on {route}') from None
        except (urllib.error.URLError, TimeoutError):
            raise RuntimeError(f'Alpaca connection failed on {route}; order outcome may be UNKNOWN') from None

    def account(self):
        x = self.call('GET', '/v2/account')
        if not isinstance(x, dict) or not x.get('id'):
            raise RuntimeError('Alpaca PAPER account identity unavailable')
        return x

    def clock(self):
        return self.call('GET', '/v2/clock')

    def positions(self):
        x = self.call('GET', '/v2/positions')
        return x if isinstance(x, list) else []

    def orders(self):
        x = self.call('GET', '/v2/orders', params={'status': 'open', 'limit': 500})
        if not isinstance(x, list):
            raise RuntimeError('Broker open orders unavailable')
        return x

    def order_by_client_id(self, cid):
        try:
            return self.call('GET', '/v2/orders:by_client_order_id', params={'client_order_id': cid})
        except RuntimeError as e:
            if 'HTTP 404' in str(e):
                return None
            raise

    def cancel(self, oid):
        return self.call('DELETE', '/v2/orders/' + oid)

    def submit(self, order):
        # The ONLY broker order submission call, hard-coded paper hostname.
        return self.call('POST', '/v2/orders', body=order)

    def stock_spy(self):
        x = self.call('GET', '/v2/stocks/SPY/snapshot', data=True, params={'feed':'iex'})
        obj = x.get('latestTrade') or x.get('minuteBar') or {}
        px = amount(obj.get('p', obj.get('c')), 'underlying quote')
        at = obj.get('t')
        if at is None or now_utc() - dt(at) > timedelta(minutes=4):
            raise RuntimeError('SPY underlying quote too old or missing')
        if px <= 0:
            raise RuntimeError('SPY underlying price invalid')
        return px

    def contracts(self, expiry, right):
        arr, token = [], None
        for _ in range(6):
            q = {'underlying_symbols':'SPY','expiration_date':expiry,'type':right,
                 'status':'active','limit':1000}
            if token: q['page_token'] = token
            obj = self.call('GET', '/v2/options/contracts', params=q)
            arr.extend(obj.get('option_contracts') or [])
            token = obj.get('next_page_token')
            if not token: break
        if token: raise RuntimeError('SPY contract list was truncated; no order submitted')
        return arr

    def snapshot(self, symbols):
        obj = self.call('GET', '/v1beta1/options/snapshots', data=True,
                        params={'symbols': ','.join(symbols), 'feed': 'indicative','limit':100})
        return obj.get('snapshots') or {}

    def bars(self, symbol, start, end=None):
        if not OCC.fullmatch(symbol):
            raise RuntimeError('SPY OCC contract required')
        result, token = [], None
        for _ in range(5):
            q = {'symbols':symbol,'timeframe':'3Min','start':start,'feed':'indicative',
                 'limit':1000,'sort':'asc'}
            # For today's contract, omit end so Alpaca caps results to the
            # latest history available under the market-data subscription.
            if end is not None: q['end'] = end
            if token: q['page_token'] = token
            obj = self.call('GET','/v1beta1/options/bars',data=True,params=q)
            result.extend((obj.get('bars') or {}).get(symbol) or [])
            token = obj.get('next_page_token')
            if not token: break
        return result


def broker_account_check(api, bound=None):
    a = api.account()
    if str(a.get('status','')).upper() != 'ACTIVE' or a.get('trading_blocked') is True or a.get('account_blocked') is True:
        raise RuntimeError('Paper account inactive or blocked')
    if bound and str(a['id']) != bound:
        raise RuntimeError('ALPACA PAPER ACCOUNT MISMATCH — refusing all orders')
    level = int(a.get('options_trading_level') or 0)
    if level < 2:
        raise RuntimeError(f'Paper account requires long-options permissions (level 2); has {level}')
    if amount(a.get('cash',0),'paper cash') < 0:
        raise RuntimeError('Negative paper balance')
    return a


def init_db():
    with sqlite3.connect(STATE, timeout=10) as db:
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('''CREATE TABLE IF NOT EXISTS cfg (name TEXT PRIMARY KEY, value TEXT NOT NULL)''')
        db.execute('''CREATE TABLE IF NOT EXISTS signals (
           event_id TEXT PRIMARY KEY, received_at TEXT NOT NULL, action TEXT NOT NULL,
           outcome TEXT NOT NULL, note TEXT NOT NULL DEFAULT '')''')
        db.execute('''CREATE TABLE IF NOT EXISTS paper_orders (
           client_order_id TEXT PRIMARY KEY, event_id TEXT NOT NULL, submitted_at TEXT NOT NULL,
           action TEXT NOT NULL CHECK(action IN ('BUY_TO_OPEN','SELL_TO_CLOSE')),
           option_symbol TEXT NOT NULL, qty INTEGER NOT NULL,
           state TEXT NOT NULL, broker_order_id TEXT, filled_qty TEXT NOT NULL DEFAULT '0',
           fill_price TEXT, filled_at TEXT, journaled INTEGER NOT NULL DEFAULT 0,
           last_update TEXT)''')
        # Backward-compatible incremental fill accounting for existing $300/1-contract DBs.
        columns={r[1] for r in db.execute('PRAGMA table_info(paper_orders)')}
        if 'journaled_qty' not in columns:
            db.execute('ALTER TABLE paper_orders ADD COLUMN journaled_qty INTEGER NOT NULL DEFAULT 0')
            db.execute('UPDATE paper_orders SET journaled_qty=CAST(filled_qty AS INTEGER) WHERE journaled=1')
        if 'journaled_notional' not in columns:
            db.execute("ALTER TABLE paper_orders ADD COLUMN journaled_notional TEXT NOT NULL DEFAULT '0'")
            db.execute('''UPDATE paper_orders SET journaled_notional=CAST(filled_qty AS REAL)*CAST(fill_price AS REAL)
                          WHERE journaled=1 AND fill_price IS NOT NULL''')
        if 'cancel_requested_at' not in columns:
            db.execute('ALTER TABLE paper_orders ADD COLUMN cancel_requested_at TEXT')


def get_cfg(k, default=None):
    init_db()
    with sqlite3.connect(STATE) as db:
        row = db.execute('SELECT value FROM cfg WHERE name=?',(k,)).fetchone()
    return row[0] if row else default


def put_cfg(k,v):
    init_db()
    with sqlite3.connect(STATE) as db:
        db.execute('INSERT OR REPLACE INTO cfg(name,value) VALUES(?,?)',(k,str(v)))


def armed():
    return get_cfg('armed','false') == 'true' and not KILL.exists()


def market_window(signal='ENTRY', current=None):
    n=(current or now_utc()).astimezone(NY)
    if n.weekday() >= 5: return False
    if signal=='ENTRY': return time(9,35) <= n.time() < time(14,30)
    # Preserve a brief grace window for KAIROS's 15:00 ET session-cutoff fill alert.
    return time(9,35) <= n.time() < time(15,2)


def quote_ok(snap, current=None):
    q = snap.get('latestQuote') or {}
    bid,ask=amount(q.get('bp'), 'option bid'),amount(q.get('ap'),'option ask')
    if bid <= 0 or ask <= bid or ask > Decimal('50'):
        raise RuntimeError('Invalid or crossed option quote')
    tick = dt(q['t'])
    lag = (current or now_utc()) - tick
    if lag < timedelta(seconds=-5) or lag > QUOTE_AGE:
        raise RuntimeError('Option quote stale')
    return bid, ask, tick


def eligible_contract(c, expiry, right):
    s=str(c.get('symbol',''))
    if not OCC.fullmatch(s) or s[3:9] != datetime.fromisoformat(expiry).strftime('%y%m%d'):
        return False
    if c.get('underlying_symbol') != 'SPY' or c.get('expiration_date')!=expiry:
        return False
    if c.get('type')!=right or c.get('tradable') is not True:
        return False
    if amount(c.get('multiplier',0))!=100:
        return False
    return True


def select_contract(api, direction, current=None):
    n=(current or now_utc()).astimezone(NY)
    if not market_window('ENTRY',n):
        raise RuntimeError('Entry outside strict 09:35–14:30 ET weekday window')
    if not api.clock().get('is_open'):
        raise RuntimeError('Alpaca reports market CLOSED; no order')
    underlying=api.stock_spy()
    right='call' if direction=='BUY_CALL' else 'put'
    expiry=n.date().isoformat()
    chain=[]
    for c in api.contracts(expiry,right):
        if eligible_contract(c,expiry,right):
            chain.append(c)
    if not chain: raise RuntimeError('No tradable SPY 0DTE contracts in Alpaca PAPER')
    chain.sort(key=lambda c: abs(amount(c['strike_price'])-underlying))
    near=chain[:22]
    snaps=api.snapshot([c['symbol'] for c in near])
    candidates=[]
    for c in near:
        try:
            bid,ask,_=quote_ok(snaps[c['symbol']])
        except (RuntimeError, KeyError, ValueError):
            continue
        spread=ask-bid
        if ask*100 > MAX_COST or spread > MAX_SPREAD or spread/ask > MAX_SPREAD_FRACTION:
            continue
        candidates.append((abs(amount(c['strike_price'])-underlying),spread,ask,c,bid))
    if not candidates: raise RuntimeError('No near-ATM 0DTE candidate passes quote, premium and spread limits')
    candidates.sort(key=lambda k:(k[0],k[1],k[2]))
    _,_,ask,c,bid=candidates[0]
    return {'symbol':c['symbol'],'bid':bid,'ask':ask,'underlying':underlying,
            'expiry':expiry,'strike':c['strike_price'],'right':right}


def contract_quantity(price, cash):
    """Long options: integer quantity whose MAXIMUM premium debit never exceeds $2,500."""
    limit = amount(price, 'entry option limit')
    available = amount(cash, 'paper cash')
    if limit < Decimal('0.01') or available <= 0:
        raise RuntimeError('No positive entry premium or paper cash')
    qty = int((min(MAX_COST, available) / (limit * 100)).to_integral_value(rounding=ROUND_DOWN))
    return min(qty, MAX_CONTRACTS)


def local_open_inventory():
    """Net broker-confirmed fills, including partial fills, not requested order quantities."""
    net = {}
    for row in orders_db():
        n = amount(row['filled_qty'] or 0, 'filled quantity')
        if n != n.to_integral_value() or n < 0 or n > int(row['qty']):
            raise RuntimeError('Local order quantity discrepancy; manual reconciliation')
        symbol = row['option_symbol']
        net[symbol] = net.get(symbol, 0) + (int(n) if row['action']=='BUY_TO_OPEN' else -int(n))
    if any(x < 0 for x in net.values()):
        raise RuntimeError('More options sold than bought according to AION; manual reconciliation')
    active = {sym:q for sym,q in net.items() if q}
    if len(active)>1:
        raise RuntimeError('Multiple managed SPY option contracts active; manual reconciliation')
    return active


def owned_position(api, managed_symbol=None):
    positions=api.positions()
    spy=[p for p in positions if str(p.get('symbol','')).startswith('SPY') and
         (p.get('asset_class')=='us_option' or OCC.fullmatch(str(p.get('symbol','')))) and
         amount(p.get('qty',0))!=0]
    expected=local_open_inventory()
    if not spy:
        if expected:
            raise RuntimeError('AION shows unclosed fills but Alpaca has no SPY position; manual reconciliation')
        return None
    if len(spy)!=1:
        raise RuntimeError('Multiple SPY options positions exist; halt worker for manual inspection')
    p=spy[0]
    if not managed_symbol or p['symbol']!=managed_symbol or p['symbol'] not in expected:
        raise RuntimeError('Unmanaged/manual SPY option position detected; refusing algorithmic order')
    expected_qty=expected[p['symbol']]
    q=amount(p.get('qty'))
    if q!=q.to_integral_value() or int(q)!=expected_qty or p.get('side')!='long':
        raise RuntimeError('Broker options quantity/side differs from verified AION fills')
    return p


def pending_orders(api):
    o=api.orders()
    if any((str(x.get('symbol','')).startswith('SPY') and x.get('asset_class')=='us_option')
           or OCC.fullmatch(str(x.get('symbol',''))) for x in o):
        raise RuntimeError('An open SPY option order already exists; no overlapping orders')


def orders_db():
    init_db()
    with sqlite3.connect(STATE) as db:
        db.row_factory=sqlite3.Row
        return [dict(r) for r in db.execute('SELECT * FROM paper_orders ORDER BY submitted_at DESC')]


def last_owned_symbol():
    inventory=local_open_inventory()
    return next(iter(inventory),None)


def ensure_no_unsettled():
    rows=orders_db()
    uncertain=[r for r in rows if r['state'] not in TERMINAL]
    if uncertain: raise RuntimeError('Pending/unknown paper order requires reconciliation; no new order')


def unique_client_id(event_id, action):
    return 'aionpv5-' + hashlib.sha256((event_id+'|'+action).encode()).hexdigest()[:29]


def submit_intent(api, event_id, action, symbol, limit, *, qty, current=None):
    if not armed(): raise RuntimeError('Not armed or kill switch exists')
    if not market_window('ENTRY' if action=='BUY_TO_OPEN' else 'EXIT',current):
        raise RuntimeError('Order rejected by ET time window')
    if not api.clock().get('is_open'):raise RuntimeError('Broker says market closed')
    if not OCC.fullmatch(symbol) or symbol[3:9] != (current or now_utc()).astimezone(NY).strftime('%y%m%d'):
        raise RuntimeError('Not a SPY 0DTE OCC symbol')
    price=amount(limit,'limit price')
    if price<Decimal('0.01') or price>Decimal('50') or price.as_tuple().exponent < -2:
        raise RuntimeError('Invalid option limit')
    if action not in ('BUY_TO_OPEN','SELL_TO_CLOSE'):
        raise RuntimeError('Only long options buy-to-open/sell-to-close allowed')
    if not isinstance(qty,int) or isinstance(qty,bool) or not 1<=qty<=MAX_CONTRACTS:
        raise RuntimeError('Invalid paper options contract quantity')
    if action=='BUY_TO_OPEN' and price*100*qty>MAX_COST:
        raise RuntimeError('Paper entry premium exceeds $2,500 debit cap')
    a=broker_account_check(api,get_cfg('paper_account_id'))
    if get_cfg('paper_account_id') is None:raise RuntimeError('Paper account not bound. Run arm first.')
    if action=='BUY_TO_OPEN' and amount(a.get('cash',0)) < price*100*qty:
        raise RuntimeError('Insufficient paper cash')
    cid=unique_client_id(event_id,action)
    init_db()
    with sqlite3.connect(STATE, timeout=10) as db:
        try:
            db.execute('''INSERT INTO paper_orders (client_order_id,event_id,submitted_at,action,option_symbol,qty,state)
                          VALUES(?,?,?,?,?,?,'UNKNOWN_SUBMISSION')''',
                       (cid,event_id,(current or now_utc()).isoformat(),action,symbol,qty))
        except sqlite3.IntegrityError:
            raise RuntimeError('Duplicate attempt: client order ID already reserved') from None
    # Broker outcome may be unknown after a timeout; NEVER auto-resubmit.
    body={'symbol':symbol,'qty':str(qty),'side':'buy' if action=='BUY_TO_OPEN' else 'sell',
          'type':'limit','time_in_force':'day','limit_price':str(price),
          'position_intent':'buy_to_open' if action=='BUY_TO_OPEN' else 'sell_to_close',
          'client_order_id':cid,'extended_hours':False}
    try:
        result=api.submit(body)
    except Exception:
        raise RuntimeError('Order outcome UNKNOWN. Worker will reconcile by client_order_id; NO retry.') from None
    if not isinstance(result,dict) or result.get('client_order_id')!=cid or result.get('symbol')!=symbol:
        raise RuntimeError('Paper broker response mismatch; order outcome UNKNOWN, inspect Alpaca')
    with sqlite3.connect(STATE) as db:
        db.execute('UPDATE paper_orders SET state=?, broker_order_id=?, last_update=? WHERE client_order_id=?',
                   (result.get('status','new'),result.get('id'),now_utc().isoformat(),cid))
    return result


def sync_orders(api):
    """Reconcile cumulative Alpaca fills incrementally; safely journal partial quantities.

    Broker filled_avg_price is cumulative, so infer the delta's average from
    cumulative notional minus previously journaled notional. Both SQLite DBs
    use deterministic idempotency keys to tolerate interruption between writes.
    """
    from aion_execution_journal import record_fill
    changed=[]
    for r in orders_db():
        # A submission response may say 'filled' before cumulative fill data is
        # persisted locally; always requery until its requested qty is journaled.
        fully_journaled=int(r.get('journaled_qty',0))==int(amount(r['filled_qty']))
        if fully_journaled and (r['state'] in {'canceled','expired','rejected','replaced'} or
                                (r['state']=='filled' and int(r['filled_qty'])==int(r['qty']))):
            continue
        result=api.order_by_client_id(r['client_order_id'])
        if result is None:
            raise RuntimeError('Alpaca order absent for reserved client id; manually reconcile before continuing')
        if result.get('client_order_id')!=r['client_order_id'] or result.get('symbol')!=r['option_symbol']:
            raise RuntimeError('Broker order reconciliation mismatch; stop processing')
        state=result.get('status','unknown')
        filled=amount(result.get('filled_qty') or 0)
        if filled!=filled.to_integral_value() or not 0<=filled<=r['qty']:
            raise RuntimeError('Unexpected fractional/overfilled option contracts')
        qty=int(filled)
        previous=int(r.get('journaled_qty',0))
        if qty<previous:
            raise RuntimeError('Alpaca cumulative filled quantity decreased unexpectedly')
        px=result.get('filled_avg_price')
        stamp=result.get('filled_at') or result.get('updated_at')
        if qty>previous:
            if not px or not stamp:
                raise RuntimeError('Broker reports options fill without price/timestamp; manual review')
            avg=amount(px,'broker cumulative fill price')
            if avg<=0:raise RuntimeError('Invalid broker cumulative options fill price')
            total_notional=avg*qty
            old_notional=amount(r.get('journaled_notional',0))
            increment=qty-previous
            delta_avg=(total_notional-old_notional)/increment
            if delta_avg<=0:raise RuntimeError('Invalid incremental broker fill price')
            # This ID identifies a cumulative broker quantity milestone. Repeat
            # after crash -> INSERT OR IGNORE, not duplicate execution journal.
            record_fill(fill_id=f'alpaca-paper-{r["client_order_id"]}-cum{qty}',filled_at=stamp,
                option_symbol=r['option_symbol'].replace('SPY','SPY   ',1),
                mode='PAPER',action=r['action'],contracts=increment,
                fill_price=float(delta_avg), fees_usd=0,
                source='ALPACA_PAPER_API_FEES_NOT_CONFIRMED',broker_order_id=result.get('id') or '')
            with sqlite3.connect(STATE) as db:
                db.execute("UPDATE paper_orders SET journaled_qty=?, journaled_notional=?, journaled=1 WHERE client_order_id=?", (qty,str(total_notional),r['client_order_id']))
        with sqlite3.connect(STATE) as db:
            db.execute("UPDATE paper_orders SET state=?, broker_order_id=?,filled_qty=?,fill_price=?, filled_at=?,last_update=? WHERE client_order_id=?",
                       (state,result.get('id'),str(qty),px,stamp,now_utc().isoformat(),r['client_order_id']))
        changed.append((r['client_order_id'],state,qty))
    return changed


def monitor_pending_order_timeouts(api):
    """Avoid stale DAY entries and surface unfilled KAIROS exits promptly."""
    outcomes=[]
    for r in orders_db():
        if r['state'] in TERMINAL:continue
        age=now_utc()-dt(r['submitted_at'])
        if r['action']=='SELL_TO_CLOSE':
            if age>timedelta(seconds=30):
                raise RuntimeError('KAIROS SELL_TO_CLOSE still open after 30s; paper exit needs manual review')
            continue
        if r.get('cancel_requested_at'):
            if now_utc()-dt(r['cancel_requested_at'])>timedelta(seconds=30):
                raise RuntimeError('PAPER entry cancellation not confirmed after 30s; manual review')
            continue
        if age>timedelta(seconds=30):
            if not r['broker_order_id']:
                raise RuntimeError('Stale PAPER entry with unknown broker identity; manual review')
            with sqlite3.connect(STATE) as db:
                db.execute('UPDATE paper_orders SET cancel_requested_at=? WHERE client_order_id=?',
                           (now_utc().isoformat(),r['client_order_id']))
            api.cancel(r['broker_order_id'])
            outcomes.append('STALE_ENTRY_CANCEL_REQUESTED '+r['option_symbol'])
    return outcomes


def accepted_events():
    if not SIGNALS.exists():
        raise RuntimeError('KAIROS simulator DB missing; start port 8001 first')
    start=get_cfg('armed_at')
    if not start:return []
    con=sqlite3.connect('file:'+urllib.parse.quote(str(SIGNALS))+'?mode=ro',uri=True,timeout=5)
    try:
        con.row_factory=sqlite3.Row
        rows=con.execute('''SELECT event_id,received_at,signal,result,strategy,symbol FROM events
                          WHERE received_at>? AND result IN ('SIGNAL_LOGGED','EXIT_INTENT_LOGGED','REVERSAL_INTENT_LOGGED')
                          ORDER BY received_at ASC LIMIT 200''',(start,)).fetchall()
        return [dict(x) for x in rows]
    finally:con.close()


def signal_seen(event_id):
    with sqlite3.connect(STATE) as db:
        return db.execute('SELECT 1 FROM signals WHERE event_id=?',(event_id,)).fetchone() is not None


def record_signal(e,outcome,note=''):
    with sqlite3.connect(STATE) as db:
        db.execute('INSERT OR IGNORE INTO signals(event_id,received_at,action,outcome,note) VALUES(?,?,?,?,?)',
                   (e['event_id'],e['received_at'],e['signal'],outcome,note[:250]))


def signal_right(signal):
    return 'C' if signal=='BUY_CALL' else 'P'


def clear_pending_reversal():
    put_cfg('pending_reversal','')


def pending_reversal():
    raw=get_cfg('pending_reversal','')
    return json.loads(raw) if raw else None


def pending_exit():
    raw=get_cfg('pending_exit','')
    return json.loads(raw) if raw else None


def request_strategy_exit(api,event_id,reason):
    """Durably queue a KAIROS exit, including if an entry is still pending.

    No second order can be placed until Alpaca confirms any entry cancellation;
    this prevents opening a new contract before the existing one is flattened.
    """
    if pending_exit():return 'EXIT_ALREADY_QUEUED'
    put_cfg('pending_exit',json.dumps({'event_id':event_id,'reason':reason}))
    return process_pending_exit(api)


def process_pending_exit(api):
    e=pending_exit()
    if not e:return None
    if not armed():return 'EXIT_PENDING_DISARMED_CHECK_BROKER'
    if not market_window('EXIT') or not api.clock().get('is_open'):
        return 'EXIT_PENDING_OUTSIDE_SESSION_CHECK_BROKER'
    rows=orders_db()
    exits=[r for r in rows if r['action']=='SELL_TO_CLOSE' and r['state'] not in TERMINAL]
    if exits:
        if any(now_utc()-dt(r['submitted_at'])>timedelta(seconds=30) for r in exits):
            raise RuntimeError('Paper exit unfilled for 30s; operator must inspect/cancel/close in Alpaca')
        return 'EXIT_WAITING_BROKER_CLOSE_FILL'
    entries=[r for r in rows if r['action']=='BUY_TO_OPEN' and r['state'] not in TERMINAL]
    if entries:
        for r in entries:
            if not r['broker_order_id']:
                raise RuntimeError('Unconfirmed entry order cannot be canceled safely; inspect Alpaca')
            if r.get('cancel_requested_at'):
                if now_utc()-dt(r['cancel_requested_at'])>timedelta(seconds=30):
                    raise RuntimeError('Entry cancellation not confirmed within 30s; manually inspect broker')
                continue
            # Reserve cancel request BEFORE transmission; never issue a second
            # destructive request after an ambiguous network timeout.
            with sqlite3.connect(STATE) as db:
                db.execute('UPDATE paper_orders SET cancel_requested_at=? WHERE client_order_id=?',
                           (now_utc().isoformat(),r['client_order_id']))
            api.cancel(r['broker_order_id'])  # Only a bot-owned Alpaca PAPER order.
        return 'EXIT_WAITING_BROKER_ENTRY_CANCEL'
    result=exit_order(api,e['event_id'],e['reason'])
    put_cfg('pending_exit','')
    return result


def exit_order(api, event_id, reason):
    pending_orders(api)
    ensure_no_unsettled()
    sym=last_owned_symbol()
    pos=owned_position(api,sym)
    if not pos:return 'NO_BROKER_POSITION'
    snap=api.snapshot([sym]).get(sym)
    if not snap:raise RuntimeError('No option quote for exit; check broker manually')
    bid,ask,_=quote_ok(snap)
    price=max(Decimal('.01'),(bid-Decimal('.02')).quantize(Decimal('.01'),rounding=ROUND_DOWN))
    q=int(amount(pos['qty']))
    result=submit_intent(api,event_id,'SELL_TO_CLOSE',sym,price,qty=q)
    return f"EXIT_ORDER_{result.get('status','submitted')} {q}x {sym} ({reason})"


def entry_order(api, e):
    pending_orders(api)
    ensure_no_unsettled()
    sym=last_owned_symbol()
    pos=owned_position(api,sym)
    if pos:return 'SKIP_POSITION_OPEN'
    item=select_contract(api,e['signal'])
    limit=(item['ask']+Decimal('.01')).quantize(Decimal('.01'),rounding=ROUND_UP)
    a=broker_account_check(api,get_cfg('paper_account_id'))
    qty=contract_quantity(limit,a.get('cash','0'))
    if not qty:
        raise RuntimeError('Near-ATM option is too expensive for $2,500 paper premium budget')
    result=submit_intent(api,e['event_id'],'BUY_TO_OPEN',item['symbol'],limit,qty=qty)
    return f"ENTRY_{result.get('status','submitted')} {qty}x {item['symbol']} @ {limit} (max ${limit*qty*100}, indicative)"


def finish_pending_reversal(api):
    pending=pending_reversal()
    if not pending:return None
    if not armed():return 'REVERSAL_DISARMED'
    if pending_exit():return 'REVERSAL_WAITING_FOR_STRATEGY_EXIT'
    if not market_window('ENTRY') or now_utc()-dt(pending['received_at'])>SIGNAL_AGE:
        clear_pending_reversal()
        return 'REVERSAL_ENTRY_SKIPPED_STALE_OR_CUTOFF'
    if any(r['state'] not in TERMINAL for r in orders_db()):
        return 'REVERSAL_WAITING_FOR_EXIT_FILL'
    pending_orders(api)
    sym=last_owned_symbol()
    if owned_position(api,sym):return 'REVERSAL_WAITING_FOR_EXIT_FILL'
    e={'event_id':pending['event_id']+'-reversal-open','signal':pending['signal']}
    result=entry_order(api,e)
    clear_pending_reversal()
    return 'REVERSAL_'+result


def process_events(api):
    results=[]
    for e in accepted_events():
        if signal_seen(e['event_id']):continue
        if e['strategy']!='KAIROS_V5' or e['symbol']!='SPY':
            record_signal(e,'REJECTED_SOURCE');continue
        lag=now_utc()-dt(e['received_at'])
        if lag>SIGNAL_AGE or lag<timedelta(seconds=-5):
            record_signal(e,'STALE_SKIP')
            results.append((e['event_id'],'STALE_SKIP'))
            if e['signal']=='EXIT' and last_owned_symbol():
                raise RuntimeError('Missed/stale KAIROS EXIT while paper position open; inspect broker immediately')
            continue
        if not armed():break
        if not market_window('ENTRY' if e['signal'].startswith('BUY') else 'EXIT'):
            record_signal(e,'SESSION_SKIP');results.append((e['event_id'],'SESSION_SKIP'));continue
        if pending_exit() and e['signal'].startswith('BUY'):
            record_signal(e,'SKIPPED_PENDING_EXIT')
            results.append((e['event_id'],'SKIPPED_PENDING_EXIT'))
            continue
        try:
            if e['signal']=='EXIT':
                clear_pending_reversal()
                outcome=request_strategy_exit(api,e['event_id'],'KAIROS_V5_EXIT')
            else:
                sym=last_owned_symbol()
                position=owned_position(api,sym)
                pending_other_side=any(r['action']=='BUY_TO_OPEN' and r['state'] not in TERMINAL and
                                       r['option_symbol'][9]!=signal_right(e['signal'])
                                       for r in orders_db())
                if (position and sym[9]!=signal_right(e['signal'])) or pending_other_side:
                    # Pine can flip directly even if the previous PAPER buy is
                    # unfilled/partially-filled. Cancel its remainder, flatten
                    # confirmed fills, then enter the new option if still fresh.
                    put_cfg('pending_reversal',json.dumps({
                        'event_id':e['event_id'],'signal':e['signal'],
                        'received_at':e['received_at']}))
                    outcome=request_strategy_exit(api,e['event_id']+'-reversal-close','KAIROS_V5_REVERSAL')
                else:
                    outcome=entry_order(api,e)
        except RuntimeError as exc:
            record_signal(e,'HALTED_ERROR',str(exc))
            KILL.write_text('Worker halted. Review broker and DB. Reason: '+str(exc),encoding='utf-8')
            results.append((e['event_id'],'HALTED_ERROR '+str(exc)))
            break
        record_signal(e,outcome)
        results.append((e['event_id'],outcome))
    return results


def risk_exit(api):
    """No independent premium TP/SL: KAIROS V5 exits control this strategy.

    The 14:45 ET deadline is an emergency attempt to avoid holding expiring
    contracts and is intentionally NOT a normal strategy exit.
    """
    if not armed() or not market_window('EXIT'):return None
    sym=last_owned_symbol()
    if not sym:return None
    if now_utc().astimezone(NY).time()<time(14,45):return None
    if pending_exit():return 'CUTOFF_EXIT_ALREADY_QUEUED'
    if any(r['action']=='SELL_TO_CLOSE' and r['state'] not in TERMINAL for r in orders_db()):
        return 'CUTOFF_EXIT_WAITING_FOR_FILL'
    return request_strategy_exit(api,'cutoff-'+now_utc().astimezone(NY).date().isoformat(),'FAILSAFE_CUTOFF_14:45')


@contextmanager
def single_worker():
    LOCK.touch(exist_ok=True)
    f=open(LOCK,'a+b')
    try:
        if sys.platform=='win32':
            import msvcrt
            f.seek(0)
            if f.read(1)==b'': f.seek(0);f.write(b'0');f.flush()
            f.seek(0)
            msvcrt.locking(f.fileno(),msvcrt.LK_NBLCK,1)
        else:
            import fcntl
            fcntl.flock(f.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        yield
    except (OSError,BlockingIOError):
        raise RuntimeError('Another AION Alpaca Paper worker is running (or lock unavailable)') from None
    finally:
        try:
            if sys.platform=='win32':
                import msvcrt
                f.seek(0);msvcrt.locking(f.fileno(),msvcrt.LK_UNLCK,1)
        finally:f.close()


def once(api):
    if not armed():
        raise RuntimeError('BOT DISARMED. Run arm to enable PAPER-only order execution.')
    a=broker_account_check(api,get_cfg('paper_account_id'))
    if str(a['id'])!=get_cfg('paper_account_id'):
        raise RuntimeError('Paper account mismatch')
    # Sync BEFORE reading signals. Avoid new orders when pending/existing.
    changed=sync_orders(api)
    monitored=monitor_pending_order_timeouts(api)
    if any(r['state']=='UNKNOWN_SUBMISSION' for r in orders_db()):
        raise RuntimeError('Unconfirmed submission; inspect Alpaca Paper and disarm')
    pending_close=process_pending_exit(api) if pending_exit() else None
    events=process_events(api)
    reversed_order=finish_pending_reversal(api) if not events and not pending_exit() else None
    # Cutoff is a documented 0DTE emergency exception to signal-only exits.
    exits=risk_exit(api) if not events and not reversed_order and not pending_exit() else None
    put_cfg('last_heartbeat',now_utc().isoformat())
    return {'events':events,'reconciled':changed,'order_timeouts':monitored,'pending_close':pending_close,'reversal':reversed_order,'risk_exit':exits}


def chart_csv(api,symbol):
    if not OCC.fullmatch(symbol):raise RuntimeError('SPY OCC contract required')
    expiry=datetime.strptime(symbol[3:9],'%y%m%d').date()
    if expiry>now_utc().astimezone(NY).date():
        raise RuntimeError('Cannot request future option chart')
    start=datetime.combine(expiry,time(9,30),NY).astimezone(timezone.utc).isoformat()
    end=datetime.combine(expiry,time(16,0),NY).astimezone(timezone.utc).isoformat()
    bars=api.bars(symbol,start,end if expiry<now_utc().astimezone(NY).date() else None)
    out=ROOT/(symbol+'_3m_bars.csv')
    with out.open('w',newline='',encoding='utf-8') as f:
        w=csv.DictWriter(f,fieldnames=['t','o','h','l','c','v'])
        w.writeheader()
        for b in bars:w.writerow({k:b.get(k) for k in ['t','o','h','l','c','v']})
    return out,len(bars)


def cli():
    parser=argparse.ArgumentParser(description='SPY 0DTE Alpaca PAPER-only execution worker')
    parser.add_argument('command',choices=['check','chain','chart','arm','disarm','once','run','sync','status'])
    parser.add_argument('--symbol',help='SPY OCC option symbol for chart, e.g. SPY261105C00745000')
    args=parser.parse_args()
    init_db()
    if args.command=='status':
        print('Paper bot armed:',armed(), '| Kill switch:',KILL.exists(),'| Account bound:',bool(get_cfg('paper_account_id')))
        print('Last heartbeat:',get_cfg('last_heartbeat','never'))
        print('Recent paper orders:')
        for x in orders_db()[:8]:print(' ',x['action'],x['option_symbol'],x['state'],x['filled_qty'],'contract(s)')
        return
    if args.command=='disarm':
        put_cfg('armed','false')
        clear_pending_reversal()
        put_cfg('pending_exit','')
        KILL.write_text('Disarmed by operator. Check any open paper positions manually.',encoding='utf-8')
        print('DISARMED; open paper orders/positions are NOT canceled or closed automatically.')
        return
    api=AlpacaPaper()
    if args.command=='check':
        a=broker_account_check(api)
        print('Alpaca PAPER account verified. Status:',a.get('status'), 'Options level:',a.get('options_trading_level'))
        print('Simulated USD cash:',a.get('cash'),'| Broker account ID:',str(a.get('id'))[-8:]+' (last 8 only)')
        print('Paper API host:',TRADING, '| Mode:',os.getenv('AION_MODE','SIMULATION'))
        print('Market open:',bool(api.clock().get('is_open')),'| Bot armed:',armed())
        return
    if args.command=='chain':
        if not market_window('ENTRY'):raise RuntimeError('Near-ATM chain selection requires 09:35–14:30 ET')
        for d in ('BUY_CALL','BUY_PUT'):
            try:
                x=select_contract(api,d)
                print(d,x['symbol'],'SPY',x['underlying'],'bid',x['bid'],'ask',x['ask'],'indicative')
            except RuntimeError as e:print(d,'unavailable:',str(e))
        return
    if args.command=='chart':
        if not args.symbol:raise RuntimeError('Supply --symbol SPYyymmddC/P########')
        path,n=chart_csv(api,args.symbol)
        print('Downloaded',n,'3-minute option bars to',path)
        return
    if args.command=='arm':
        a=broker_account_check(api)
        if not SIGNALS.exists():raise RuntimeError('Signal simulator DB missing; start port 8001')
        if api.positions() or api.orders():
            raise RuntimeError('Existing paper positions/open orders detected: reconcile manually before arming')
        if any(x['state'] not in TERMINAL or x['journaled']==0 and x['state']=='filled' for x in orders_db()):
            raise RuntimeError('Unreconciled historical bot orders; sync or inspect before re-arming')
        prior_account=get_cfg('paper_account_id')
        if prior_account is not None and prior_account != str(a['id']):
            raise RuntimeError('Existing paper account binding differs; refusing re-arm')
        if not sys.stdin.isatty():raise RuntimeError('Arm requires an interactive terminal')
        print('ONLY Alpaca PAPER API can receive orders. Real Webull/Alpaca LIVE are not used.')
        print('Trading mode must remain SIMULATION. Max $2,500 TOTAL paper premium per entry, <=25 contracts.')
        print('Exits: KAIROS V5 exit/reversal signals; 14:45 ET emergency failsafe.')
        print('The worker can submit PAPER orders automatically from FUTURE KAIROS V5 alerts.')
        word=input('Type ARM ALPACA PAPER to enable this automation: ').strip()
        if word!='ARM ALPACA PAPER':raise RuntimeError('Arm declined')
        put_cfg('paper_account_id',a['id'])
        put_cfg('armed_at',now_utc().isoformat())
        clear_pending_reversal()
        put_cfg('pending_exit','')
        KILL.unlink(missing_ok=True)
        put_cfg('armed','true')
        print('ARMED: future KAIROS V5 signals only, paper API only. Start with: python aion_alpaca_paper_algo.py run')
        return
    if args.command=='sync':
        bound=get_cfg('paper_account_id')
        if not bound:raise RuntimeError('No paper account binding; arm first')
        broker_account_check(api,bound)
        print('Reconciled:',sync_orders(api))
        return
    with single_worker():
        if args.command=='once':
            print(json.dumps(once(api),indent=2))
            return
        if args.command=='run':
            print('AION Alpaca PAPER worker active; no orders outside strict rules. Ctrl+C to stop.')
            while True:
                try:
                    if not armed():
                        print('Worker disarmed or kill switch triggered; stopping.')
                        break
                    result=once(api)
                    if result['events'] or result['order_timeouts'] or result['risk_exit'] or (result['reversal'] and not result['reversal'].endswith('WAITING_FOR_EXIT_FILL')) or (result['pending_close'] and not result['pending_close'].startswith('EXIT_WAITING')):
                        print(json.dumps(result),flush=True)
                except RuntimeError as e:
                    KILL.write_text('Worker halted: '+str(e),encoding='utf-8')
                    print('HALTED:',e,'| Fix and manually re-arm after reconciling paper orders.',flush=True)
                    break
                time_module.sleep(5)


if __name__=='__main__':
    try:cli()
    except KeyboardInterrupt:
        print('\nStopped. Check Alpaca Paper for open positions and orders.')
    except (RuntimeError,ValueError) as e:
        print('ERROR:',e,file=sys.stderr)
        sys.exit(1)