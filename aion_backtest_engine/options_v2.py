"""0DTE historical quote replay. NO option premium interpolation or theoretical marks.
Only real imported/provider option bid/ask quotes are eligible for a fill.
"""
from __future__ import annotations
import io
import math
import re
from collections import defaultdict
from datetime import datetime
from urllib.parse import quote
import numpy as np
import pandas as pd
from .market_v2 import get_json

NY='America/New_York'
CONTRACT_RE=re.compile(r'^O:([A-Z]{1,7})(\d{2})(\d{2})(\d{2})([CP])(\d{8})$')


def make_0dte_contract(symbol,time,direction,underlying,step=.5):
    if symbol not in {'SPY','QQQ','IWM'}:
        raise ValueError('Automatic ATM 0DTE contract selection currently supports SPY, QQQ and IWM; other tickers require a contract-aware signal/quote source')
    if not (0.01<=step<=10):raise ValueError('Invalid strike increment')
    dt=pd.Timestamp(time)
    if dt.tz is None:raise ValueError('Timezone-aware signal required')
    dt=dt.tz_convert(NY)
    strike=math.floor(float(underlying)/step+.5)*step
    kind='C' if direction==1 else 'P'
    return f'O:{symbol}{dt.strftime("%y%m%d")}{kind}{round(strike*1000):08d}'


def validate_contract(contract,signal_date=None,symbol=None,direction=None):
    match=CONTRACT_RE.fullmatch(contract)
    if not match:raise ValueError('Invalid OCC option contract: expected O:SPYyymmddC00100000')
    root,yy,mm,dd,cp,_=match.groups()
    expiration=pd.Timestamp(f'20{yy}-{mm}-{dd}').date()
    if symbol and root!=symbol:raise ValueError('Contract underlying ticker differs from selected symbol')
    if signal_date and expiration!=signal_date:raise ValueError('Not a same-day-expiration contract')
    if direction and (cp!='C' if direction==1 else cp!='P'):
        raise ValueError('Long stock signals must use calls, short signals puts in this long-premium model')
    return expiration


def quote_table(db):
    db.execute('''CREATE TABLE IF NOT EXISTS option_quotes(
        contract TEXT NOT NULL,t_ms INTEGER NOT NULL,bid REAL NOT NULL,ask REAL NOT NULL,
        provider TEXT NOT NULL,PRIMARY KEY(contract,t_ms))''')
    db.execute('CREATE INDEX IF NOT EXISTS quote_ct_time ON option_quotes(contract,t_ms)')
    db.commit()


def normalize_quotes(df):
    df=df.copy()
    df.columns=[str(c).strip().lower() for c in df.columns]
    if 'time' in df and 'timestamp' not in df:df.rename(columns={'time':'timestamp'},inplace=True)
    if not all(c in df for c in ['timestamp','contract','bid','ask']):
        raise ValueError('Options CSV requires timestamp,contract,bid,ask columns')
    if not 1<=len(df)<=500000:raise ValueError('Quote CSV needs 1–500,000 rows')
    t=pd.to_datetime(df.timestamp,utc=True,errors='coerce')
    if t.isna().any():raise ValueError('Quote timestamp must have a valid time with timezone offset')
    # Implicit UTC is disallowed; an offset must be explicitly supplied.
    if any(not re.search(r'(Z|[+-]\d\d:?\d\d)$',str(s).strip()) for s in df.timestamp):
        raise ValueError('Option quote timestamp must include Z or a +/- timezone offset')
    b=pd.to_numeric(df.bid,errors='coerce');a=pd.to_numeric(df.ask,errors='coerce')
    if (b<0).any() or (a<=0).any() or (a<b).any() or not np.isfinite(b).all() or not np.isfinite(a).all():
        raise ValueError('Quotes must have nonnegative bid, positive ask, and ask >= bid')
    for c in df.contract:validate_contract(str(c).strip().upper())
    out=pd.DataFrame({'contract':df.contract.astype(str).str.strip().str.upper(),
                      't_ms':t.astype('int64')//1_000_000,'bid':b,'ask':a})
    # No duplicate same timestamp with different quotes; last update wins.
    return out.drop_duplicates(['contract','t_ms'],keep='last')


def csv_quotes(content):
    if len(content)>40_000_000:raise ValueError('Quote CSV exceeds 40 MB')
    return normalize_quotes(pd.read_csv(io.BytesIO(content)))


def store_quotes(db,quotes,provider='User quote CSV'):
    quote_table(db)
    rows=[(str(r.contract),int(r.t_ms),float(r.bid),float(r.ask),provider) for r in quotes.itertuples()]
    db.executemany('''INSERT INTO option_quotes(contract,t_ms,bid,ask,provider) VALUES(?,?,?,?,?)
        ON CONFLICT(contract,t_ms) DO UPDATE SET bid=excluded.bid,ask=excluded.ask,provider=excluded.provider''',rows)
    db.commit();return len(rows)


def provider_quotes(contract,begin,end):
    """Retrieve genuine Massive historical NBBO records for a single contract.
    API endpoint requires separate subscription; the server holds the key.
    """
    validate_contract(contract)
    a=int(pd.Timestamp(begin).timestamp()*1e9);b=int(pd.Timestamp(end).timestamp()*1e9)
    if a>=b:raise ValueError('Quote download end must be after start')
    rows=get_json('/v3/quotes/'+quote(contract,safe=':'),{
        'timestamp.gte':a,'timestamp.lte':b,'sort':'timestamp','order':'asc','limit':50000})
    if not rows:raise ValueError('Provider returned no historical option quotes. Plan may not include historical NBBO data')
    items=[]
    for item in rows:
        t=item.get('sip_timestamp') or item.get('participant_timestamp')
        if t is None:continue
        items.append({'contract':contract,'t_ms':int(t)//1_000_000,
                      'bid':item.get('bid_price'),'ask':item.get('ask_price')})
    if not items:raise ValueError('No timestamped NBBO quotes returned')
    x=pd.DataFrame(items)
    x=x.replace([np.inf,-np.inf],np.nan).dropna()
    x=x[(x.bid>=0)&(x.ask>0)&(x.ask>=x.bid)]
    if x.empty:raise ValueError('No valid two-sided option quotes')
    return x.drop_duplicates(['contract','t_ms'],keep='last')


def replay_0dte(db,stock_trades,symbol,contracts='atm',strike_step=.5,contracts_qty=1,
                 fee_per_contract=0.65,slippage_per_contract=0.01,max_quote_delay_s=60,
                 auto_download=False,exit_mode='signal',premium_stop_pct=.25,premium_reward_r=1.0):
    """Signal-time exit replay: buy at actual ask on/after entry, sell at actual
    bid on/after stock-signal exit. This does not model option-premium stop/target
    or market impact, and is NOT a full Pine-accurate options simulation.
    Missing quotes result in SKIPPED trades, never theoretical pricing.
    """
    quote_table(db)
    if exit_mode not in ('signal','premium_bracket'):raise ValueError('Invalid option exit mode')
    if not (.05<=premium_stop_pct<=.95 and .1<=premium_reward_r<=10):raise ValueError('Invalid option premium stop or reward')
    if not (1<=contracts_qty<=100 and 0<=fee_per_contract<=20 and 0<=slippage_per_contract<=2 and 1<=max_quote_delay_s<=120):
        raise ValueError('Invalid option execution settings')
    if len(stock_trades)>1000:raise ValueError('Maximum 1000 underlying trades per 0DTE run')
    valid=[];errors=[];requests={}
    for t in stock_trades:
        try:
            enter=pd.Timestamp(t['entry_at']).tz_convert(NY);leave=pd.Timestamp(t['exit_at']).tz_convert(NY)
            d=1 if str(t['side']).upper()=='LONG' else -1
            if enter.date()!=leave.date() or leave<=enter: raise ValueError('non-intraday signal')
            contract=(str(t.get('contract') or '').upper() if contracts=='signal' else
                      make_0dte_contract(symbol,enter,d,t.get('underlying_entry_price',t.get('entry_price')),strike_step))
            validate_contract(contract,enter.date(),symbol,d)
            valid.append((t,enter,leave,contract))
            if contract not in requests:requests[contract]=[enter,leave]
            else:
                requests[contract][0]=min(requests[contract][0],enter)
                requests[contract][1]=max(requests[contract][1],leave)
        except (ValueError,KeyError,TypeError) as ex:
            errors.append({'entry_at':t.get('entry_at'),'reason':'Invalid signal: '+str(ex)[:100]})
    if auto_download:
        if len(requests)>20:raise ValueError('Auto-download is limited to 20 unique option contracts per run; use a shorter backtest window or import quotes')
        for contract,(first,last) in requests.items():
            first_ms=int(first.timestamp()*1000);last_ms=int(last.timestamp()*1000)
            have_in=db.execute('SELECT COUNT(*) FROM option_quotes WHERE contract=? AND t_ms BETWEEN ? AND ?',
                               (contract,first_ms,first_ms+max_quote_delay_s*1000)).fetchone()[0]
            have_out=db.execute('SELECT COUNT(*) FROM option_quotes WHERE contract=? AND t_ms BETWEEN ? AND ?',
                                (contract,last_ms,last_ms+max_quote_delay_s*1000)).fetchone()[0]
            if have_in and have_out:continue
            try:
                q=provider_quotes(contract,first-pd.Timedelta(seconds=5),last+pd.Timedelta(seconds=max_quote_delay_s))
                store_quotes(db,q,'Massive NBBO historical quotes')
            except ValueError as ex:errors.append({'contract':contract,'reason':'Provider: '+str(ex)[:110]})
    # The limit on quote age is measured from desired fill time forward. Using an
    # earlier stale quote is not allowed; current midpoint is never synthesized.
    trades=[]
    for signal,entry,exit_,contract in valid:
        a=int(entry.timestamp()*1000);b=int(exit_.timestamp()*1000);delta=int(max_quote_delay_s*1000)
        q1=db.execute('''SELECT * FROM option_quotes WHERE contract=? AND t_ms BETWEEN ? AND ? ORDER BY t_ms LIMIT 1''',(contract,a,a+delta)).fetchone()
        q2=db.execute('''SELECT * FROM option_quotes WHERE contract=? AND t_ms BETWEEN ? AND ? ORDER BY t_ms LIMIT 1''',(contract,b,b+delta)).fetchone()
        exit_reason='underlying signal exit'
        max_gap=0.0
        if q1 and exit_mode=='premium_bracket':
            stop=(float(q1['ask'])+slippage_per_contract)*(1-premium_stop_pct)
            target=(float(q1['ask'])+slippage_per_contract)*(1+premium_stop_pct*premium_reward_r)
            # Any observed NBBO bid crossing bracket triggers a fill at the actual
            # observed bid, not an invented exact stop or limit premium.
            raw=db.execute('''SELECT * FROM option_quotes WHERE contract=? AND t_ms>? AND t_ms<=? ORDER BY t_ms''',
                           (contract,q1['t_ms'],b)).fetchall()
            stamps=[q1['t_ms']]+[r['t_ms'] for r in raw]
            if len(stamps)>1:max_gap=max(np.diff(stamps))/1000
            for r in raw:
                bid=float(r['bid'])
                if bid<=stop or bid>=target:
                    q2=r;exit_reason='option premium stop' if bid<=stop else 'option premium target'
                    break
        if not q1 or not q2:
            errors.append({'entry_at':entry.isoformat(),'contract':contract,'reason':'Missing fresh bid/ask quote at entry or exit'})
            continue
        buy=float(q1['ask'])+slippage_per_contract;sell=max(0.0,float(q2['bid'])-slippage_per_contract)
        if buy<=0 or float(q1['ask'])<float(q1['bid']) or float(q2['ask'])<float(q2['bid']):
            errors.append({'contract':contract,'reason':'Invalid option quote'});continue
        pnl=(sell-buy)*100*contracts_qty-2*fee_per_contract*contracts_qty
        trades.append({'date':pd.Timestamp(int(q2['t_ms']),unit='ms',tz='UTC').tz_convert(NY).isoformat(),
                       'entry_at':entry.isoformat(),'exit_at':exit_.isoformat(),'side':signal['side'],
                       'contract':contract,'entry_price':round(buy,4),'exit_price':round(sell,4),
                       'qty':contracts_qty,'pnl':round(pnl,4),'entry_quote_delay_s':round((q1['t_ms']-a)/1000,3),
                       'exit_quote_delay_s':round((q2['t_ms']-b)/1000,3) if exit_reason=='underlying signal exit' else None,
                       'exit_reason':exit_reason,'max_observed_quote_gap_s':round(max_gap,2),
                       'quote_source':q1['provider']})
    return trades,{'requested':len(stock_trades),'filled':len(trades),'skipped':len(stock_trades)-len(trades),
                   'errors':errors[:30], 'quote_model':f'Ask entry / bid exit; actual NBBO; 100 multiplier; per-side fees and slippage; exit mode {exit_mode}',
                   'note':'Quote replay exits on underlying signal or observed premium stop/target (if enabled), whichever first. P&L is not equivalent to live fills; missing quotes exclude trades and bracket sampling is not continuous.'}
