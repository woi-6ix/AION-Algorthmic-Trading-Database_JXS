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
MAX_COST = Decimal('300')
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
    return time(9,35) <= n.time() < time(15,0)


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


def owned_position(api, managed_symbol=None):
    positions=api.positions()
    spy=[p for p in positions if str(p.get('symbol','')).startswith('SPY') and
         (p.get('asset_class')=='us_option' or OCC.fullmatch(str(p.get('symbol','')))) and
         amount(p.get('qty',0))!=0]
    if not spy: return None
    if len(spy)!=1:
        raise RuntimeError('Multiple SPY options positions exist; halt worker for manual inspection')
    p=spy[0]
    if not managed_symbol or p['symbol']!=managed_symbol:
        raise RuntimeError('Unmanaged/manual SPY option position detected; refusing algorithmic order')
    if amount(p.get('qty'))!=1 or p.get('side')!='long':
        raise RuntimeError('Position quantity/side unexpected; manual reconciliation required')
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
    rows=orders_db()
    entries=[r for r in rows if r['action']=='BUY_TO_OPEN' and r['state']=='filled']
    if not entries:return None
    entry=entries[0]
    exits=[r for r in rows if r['action']=='SELL_TO_CLOSE' and r['option_symbol']==entry['option_symbol'] and r['state']=='filled' and r['submitted_at']>=entry['submitted_at']]
    return None if exits else entry['option_symbol']


def ensure_no_unsettled():
    rows=orders_db()
    uncertain=[r for r in rows if r['state'] not in TERMINAL]
    if uncertain: raise RuntimeError('Pending/unknown paper order requires reconciliation; no new order')


def unique_client_id(event_id, action):
    return 'aionpv5-' + hashlib.sha256((event_id+'|'+action).encode()).hexdigest()[:29]


def submit_intent(api, event_id, action, symbol, limit, current=None):
    if not armed(): raise RuntimeError('Not armed or kill switch exists')
    if not market_window('ENTRY' if action=='BUY_TO_OPEN' else 'EXIT',current):
        raise RuntimeError('Order rejected by ET time window')
    if not api.clock().get('is_open'):raise RuntimeError('Broker says market closed')
    if not OCC.fullmatch(symbol) or symbol[3:9] != (current or now_utc()).astimezone(NY).strftime('%y%m%d'):
        raise RuntimeError('Not a SPY 0DTE OCC symbol')
    price=amount(limit,'limit price')
    if price<Decimal('0.01') or price>Decimal('50') or price.as_tuple().exponent < -2:
        raise RuntimeError('Invalid option limit')
    if action=='BUY_TO_OPEN' and price*100>MAX_COST:
        raise RuntimeError('Paper premium exceeds $300 risk cap')
    a=broker_account_check(api,get_cfg('paper_account_id'))
    if get_cfg('paper_account_id') is None:raise RuntimeError('Paper account not bound. Run arm first.')
    if action=='BUY_TO_OPEN' and amount(a.get('cash',0)) < price*100:
        raise RuntimeError('Insufficient paper cash')
    cid=unique_client_id(event_id,action)
    init_db()
    with sqlite3.connect(STATE, timeout=10) as db:
        try:
            db.execute('''INSERT INTO paper_orders (client_order_id,event_id,submitted_at,action,option_symbol,qty,state)
                          VALUES(?,?,?,?,?,1,'UNKNOWN_SUBMISSION')''',
                       (cid,event_id,(current or now_utc()).isoformat(),action,symbol))
        except sqlite3.IntegrityError:
            raise RuntimeError('Duplicate attempt: client order ID already reserved') from None
    # Broker outcome may be unknown after a timeout; NEVER auto-resubmit.
    body={'symbol':symbol,'qty':'1','side':'buy' if action=='BUY_TO_OPEN' else 'sell',
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
    from aion_execution_journal import record_fill
    changed=[]
    for r in orders_db():
        if r['state'] in {'rejected','canceled','expired'} and not r['filled_at']:
            continue
        result=api.order_by_client_id(r['client_order_id'])
        if result is None:
            changed.append((r['client_order_id'],'NOT_FOUND_CHECK_MANUALLY'))
            continue
        if result.get('client_order_id')!=r['client_order_id'] or result.get('symbol')!=r['option_symbol']:
            raise RuntimeError('Broker order reconciliation mismatch; stop processing')
        state=result.get('status','unknown')
        filled_qty=amount(result.get('filled_qty') or 0)
        px=result.get('filled_avg_price')
        ts=result.get('filled_at')
        with sqlite3.connect(STATE) as db:
            db.execute('''UPDATE paper_orders SET state=?, broker_order_id=?,filled_qty=?,fill_price=?,
                         filled_at=?,last_update=? WHERE client_order_id=?''',
                       (state,result.get('id'),str(filled_qty),px,ts,now_utc().isoformat(),r['client_order_id']))
        if filled_qty==1 and px and ts:
            # Journal only confirmed full fills; source must never be DEMO.
            journaled=record_fill(fill_id='alpaca-paper-'+r['client_order_id'],filled_at=ts,
                option_symbol=r['option_symbol'].replace('SPY','SPY   ',1),mode='PAPER',action=r['action'],contracts=1,
                fill_price=float(px), fees_usd=0,source='ALPACA_PAPER_API_FEES_NOT_CONFIRMED',
                broker_order_id=result.get('id') or '')
            with sqlite3.connect(STATE) as db:
                db.execute('UPDATE paper_orders SET journaled=1 WHERE client_order_id=?',(r['client_order_id'],))
            changed.append((r['client_order_id'],state,'journaled' if journaled else 'already_journaled'))
        else:changed.append((r['client_order_id'],state))
    return changed


def accepted_events():
    if not SIGNALS.exists():
        raise RuntimeError('KAIROS simulator DB missing; start port 8001 first')
    start=get_cfg('armed_at')
    if not start:return []
    con=sqlite3.connect('file:'+urllib.parse.quote(str(SIGNALS))+'?mode=ro',uri=True,timeout=5)
    try:
        con.row_factory=sqlite3.Row
        rows=con.execute('''SELECT event_id,received_at,signal,result,strategy,symbol FROM events
                          WHERE received_at>? AND result IN ('SIGNAL_LOGGED','EXIT_INTENT_LOGGED')
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


def exit_order(api, event_id, reason):
    pending_orders(api)
    ensure_no_unsettled()
    sym=last_owned_symbol()
    pos=owned_position(api,sym)
    if not pos:return 'NO_BROKER_POSITION'
    snap=api.snapshot([sym]).get(sym)
    if not snap:raise RuntimeError('No option quote for exit; check broker manually')
    bid,ask,_=quote_ok(snap)
    # A marketable limit at/below indicated bid; never unlimited market order.
    price=max(Decimal('.01'),(bid-Decimal('.02')).quantize(Decimal('.01'),rounding=ROUND_DOWN))
    result=submit_intent(api,event_id,'SELL_TO_CLOSE',sym,price)
    return 'EXIT_ORDER_'+result.get('status','submitted')+' ('+reason+')'


def entry_order(api, e):
    pending_orders(api)
    ensure_no_unsettled()
    sym=last_owned_symbol()
    pos=owned_position(api,sym)
    if pos:return 'SKIP_POSITION_OPEN'
    item=select_contract(api,e['signal'])
    ask=item['ask']
    # Limit 1 cent above indicative ask; maximum premium cap remains hard.
    limit=(ask+Decimal('.01')).quantize(Decimal('.01'),rounding=ROUND_UP)
    result=submit_intent(api,e['event_id'],'BUY_TO_OPEN',item['symbol'],limit)
    return f"ENTRY_{result.get('status','submitted')} {item['symbol']} @ {limit} (indicative)"


def process_events(api):
    results=[]
    for e in accepted_events():
        if signal_seen(e['event_id']):continue
        if e['strategy']!='KAIROS_V5' or e['symbol']!='SPY':
            record_signal(e,'REJECTED_SOURCE');continue
        lag=now_utc()-dt(e['received_at'])
        if lag> SIGNAL_AGE or lag<timedelta(seconds=-5):
            record_signal(e,'STALE_SKIP');results.append((e['event_id'],'STALE_SKIP'));continue
        if not armed():break
        if not market_window('ENTRY' if e['signal'].startswith('BUY') else 'EXIT'):
            record_signal(e,'SESSION_SKIP');results.append((e['event_id'],'SESSION_SKIP'));continue
        try:
            outcome=entry_order(api,e) if e['signal'].startswith('BUY') else exit_order(api,e['event_id'],'KAIROS_EXIT')
        except RuntimeError as exc:
            # If a request may have reached the broker, never auto-retry.
            record_signal(e,'HALTED_ERROR',str(exc))
            KILL.write_text('Worker halted. Review broker and DB. Reason: '+str(exc),encoding='utf-8')
            results.append((e['event_id'],'HALTED_ERROR '+str(exc)))
            break
        record_signal(e,outcome)
        results.append((e['event_id'],outcome))
    return results


def risk_exit(api):
    if not armed() or not market_window('EXIT'):return None
    sym=last_owned_symbol()
    if not sym:return None
    ensure_no_unsettled()
    pos=owned_position(api,sym)
    if not pos:return None
    today=now_utc().astimezone(NY)
    # Never carry 0DTE paper options beyond the configured cutoff.
    if today.time()>=time(14,45):
        return exit_order(api,'cutoff-'+today.date().isoformat(),'AUTO_CUTOFF_14:45')
    rows=orders_db()
    entry=next((r for r in rows if r['option_symbol']==sym and r['action']=='BUY_TO_OPEN' and r['state']=='filled'),None)
    if not entry or not entry['fill_price']:return None
    snap=api.snapshot([sym]).get(sym)
    if not snap:return None
    bid,_,_=quote_ok(snap)
    paid=amount(entry['fill_price'])
    # Explicit, adjustable paper-only premium rules (NOT a replication of Pine R exits).
    tp=amount(os.getenv('AION_PAPER_TAKE_PROFIT_PCT','0.30'))
    sl=amount(os.getenv('AION_PAPER_STOP_LOSS_PCT','0.25'))
    if not (Decimal('0.05')<=tp<=Decimal('2') and Decimal('0.05')<=sl<=Decimal('0.9')):
        raise RuntimeError('Invalid paper TP/SL percentage values')
    if bid>=paid*(1+tp):return exit_order(api,'tp-'+today.isoformat(),'PAPER_PREMIUM_TP')
    if bid<=paid*(1-sl):return exit_order(api,'sl-'+today.isoformat(),'PAPER_PREMIUM_STOP')
    return None


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
    if any(r['state']=='UNKNOWN_SUBMISSION' for r in orders_db()):
        raise RuntimeError('Unconfirmed submission; inspect Alpaca Paper and disarm')
    events=process_events(api)
    # No silent paper broker trades when worker is armed without validated signals.
    exits=risk_exit(api) if not events else None
    put_cfg('last_heartbeat',now_utc().isoformat())
    return {'events':events,'reconciled':changed,'risk_exit':exits}


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
        print('Trading mode must remain SIMULATION. One SPY 0DTE contract, max $300.')
        print('The worker can submit PAPER orders automatically from FUTURE KAIROS V5 alerts.')
        word=input('Type ARM ALPACA PAPER to enable this automation: ').strip()
        if word!='ARM ALPACA PAPER':raise RuntimeError('Arm declined')
        put_cfg('paper_account_id',a['id'])
        put_cfg('armed_at',now_utc().isoformat())
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
                    if result['events'] or result['risk_exit']:
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
