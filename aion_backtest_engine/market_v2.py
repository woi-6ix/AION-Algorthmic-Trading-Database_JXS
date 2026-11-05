"""Historical price source adapters and CSV normalisation for AION.
Automatic providers: Yahoo (plan-limited) and Massive (user-supplied API key).
TradingView sync runs through server.py with separate authorization.
"""
from __future__ import annotations
import io
import os
import re
from datetime import date
from urllib.parse import urlencode, urlparse, parse_qsl
from urllib.request import Request, urlopen
import json
import pandas as pd
import numpy as np

INTERVALS={'1m','2m','3m','5m','15m','30m','1h','1d','1wk'}


def normalise_bars(df,interval='5m',tz='America/New_York'):
    if interval not in INTERVALS:raise ValueError('Unsupported interval')
    df=df.copy()
    ren={c:c.strip().lower().replace(' ','_') for c in df.columns}
    df.rename(columns=ren,inplace=True)
    if 'datetime' in df and 'timestamp' not in df:df.rename(columns={'datetime':'timestamp'},inplace=True)
    if 'date' in df and 'timestamp' not in df:df.rename(columns={'date':'timestamp'},inplace=True)
    if 'time' in df and 'timestamp' not in df:df.rename(columns={'time':'timestamp'},inplace=True)
    wanted=['open','high','low','close','volume']
    if any(c not in df for c in ['timestamp']+wanted):raise ValueError('CSV needs timestamp,open,high,low,close,volume columns')
    if len(df)>500000 or len(df)<2:raise ValueError('CSV must contain 2–500,000 OHLCV rows')
    times=pd.to_datetime(df['timestamp'],errors='coerce',utc=False)
    if times.isna().any():raise ValueError('Unparseable OHLCV timestamp')
    # Uniform offset strings are supported; mixed offsets must be pre-normalized by user.
    if not isinstance(times.dtype,pd.DatetimeTZDtype):
        times=times.dt.tz_localize(tz,ambiguous='raise',nonexistent='raise')
    times=times.dt.tz_convert('America/New_York')
    arr=df[wanted].apply(pd.to_numeric,errors='coerce')
    if not np.isfinite(arr.to_numpy()).all():raise ValueError('Non-finite OHLCV value')
    if (arr[['open','high','low','close']]<=0).any().any() or (arr['volume']<0).any():raise ValueError('Negative/zero prices or negative volume')
    if ((arr.high<arr[['open','close','low']].max(axis=1)) | (arr.low>arr[['open','close']].min(axis=1))).any():
        raise ValueError('OHLC inconsistency: high below an OHLC value or low above one')
    arr.index=pd.DatetimeIndex(times)
    arr.columns=[c.title() for c in arr.columns]
    return arr.sort_index()[~arr.index.duplicated(keep='last')]


def csv_bars(content,interval='5m',tz='America/New_York'):
    if len(content)>40_000_000:raise ValueError('Maximum 40 MB OHLCV CSV')
    return normalise_bars(pd.read_csv(io.BytesIO(content)),interval,tz)


def get_json(path,params,base='https://api.massive.com'):
    key=os.getenv('AION_MASSIVE_API_KEY','').strip()
    if not key:raise ValueError('Massive historical data requires AION_MASSIVE_API_KEY in your environment')
    query=urlencode({**params,'apiKey':key})
    url=base+path+'?'+query
    out=[]
    while True:
        target=urlparse(url)
        if target.scheme!='https' or target.netloc!='api.massive.com' or not target.path.startswith(('/v2/aggs/','/v3/quotes/')):
            raise ValueError('Unsafe pagination link received from provider')
        req=Request(url,headers={'Accept':'application/json','User-Agent':'AION-Quant-Terminal/0.2'})
        try:
            with urlopen(req,timeout=35) as response:data=json.load(response)
        except Exception as e:
            raise ValueError('Massive market data request failed: '+str(e).replace(key,'[REDACTED]')[:160])
        if data.get('status')=='ERROR':raise ValueError('Provider rejected request: '+str(data.get('error','unknown'))[:150])
        out.extend(data.get('results') or [])
        if len(out)>250000:raise ValueError('Too many market data rows; choose a shorter date range')
        nxt=data.get('next_url')
        if not nxt:break
        # Keep API key in server and carry it through provider pagination.
        if len(out)==0:raise ValueError('Empty provider page with pagination')
        if not urlparse(nxt).query.endswith('apiKey='+key):
            sep='&' if '?' in nxt else '?'
            nxt=nxt+sep+urlencode({'apiKey':key})
        url=nxt
    return out


def massive_stock_history(symbol,interval,start,end):
    if not re.fullmatch(r'[A-Z0-9.]{1,12}',symbol):raise ValueError('Unsupported symbol for Massive')
    if interval not in INTERVALS:raise ValueError('Invalid bar interval')
    step={'1m':1,'2m':2,'3m':3,'5m':5,'15m':15,'30m':30,'1h':60,'1d':1,'1wk':1}[interval]
    span='minute' if interval not in ('1d','1wk') else 'day' if interval=='1d' else 'week'
    start=date.fromisoformat(start);end=date.fromisoformat(end)
    if end<start or (end-start).days>3650:raise ValueError('Invalid start/end date range')
    rows=get_json(f'/v2/aggs/ticker/{symbol}/range/{step}/{span}/{start}/{end}',{'adjusted':'false','sort':'asc','limit':50000})
    if not rows:raise ValueError('No Massive OHLCV results; check data entitlements and selected dates')
    # Timestamp in milliseconds, always bar opening time.
    x=pd.DataFrame(rows)
    times=pd.to_datetime(x['t'],unit='ms',utc=True).dt.tz_convert('America/New_York')
    df=pd.DataFrame({'Open':x.o.to_numpy(dtype=float),'High':x.h.to_numpy(dtype=float),
                     'Low':x.l.to_numpy(dtype=float),'Close':x.c.to_numpy(dtype=float),
                     'Volume':x.v.to_numpy(dtype=float)},index=pd.DatetimeIndex(times))
    if len(df)>250000:raise ValueError('Date range produced excessive bars')
    return df.sort_index(), {'source':'Massive historical aggregates (subscription-dependent)','realtime':False,
          'notice':'Unadjusted OHLCV; actual data permissions, feed delay and depth depend on subscription.',
          'last_bar':df.index[-1].isoformat()}


def store_bars(db,df,symbol,interval,source):
    db.execute('''CREATE TABLE IF NOT EXISTS historical_bars(
        symbol TEXT NOT NULL, interval TEXT NOT NULL, t TEXT NOT NULL,
        o REAL,h REAL,l REAL,c REAL,v REAL,source TEXT NOT NULL,
        PRIMARY KEY(symbol,interval,t))''')
    rows=[(symbol,interval,t.isoformat(),float(b.Open),float(b.High),float(b.Low),float(b.Close),float(b.Volume),source)
          for t,b in df.iterrows()]
    db.executemany('''INSERT INTO historical_bars(symbol,interval,t,o,h,l,c,v,source) VALUES(?,?,?,?,?,?,?,?,?)
        ON CONFLICT(symbol,interval,t) DO UPDATE SET o=excluded.o,h=excluded.h,l=excluded.l,
        c=excluded.c,v=excluded.v,source=excluded.source''',rows)
    db.commit()
    return len(rows)


def load_bars(db,symbol,interval,start=None,end=None):
    db.execute('''CREATE TABLE IF NOT EXISTS historical_bars(
        symbol TEXT NOT NULL, interval TEXT NOT NULL, t TEXT NOT NULL,
        o REAL,h REAL,l REAL,c REAL,v REAL,source TEXT NOT NULL,
        PRIMARY KEY(symbol,interval,t))''')
    rows=db.execute('SELECT * FROM historical_bars WHERE symbol=? AND interval=? ORDER BY t',(symbol,interval)).fetchall()
    if not rows:raise ValueError('No cached historical bars: download market data or import an OHLCV CSV')
    x=pd.DataFrame([dict(z) for z in rows]);timestamps=pd.to_datetime(x.t,utc=True).dt.tz_convert('America/New_York')
    df=pd.DataFrame({'Open':x.o.to_numpy(float),'High':x.h.to_numpy(float),'Low':x.l.to_numpy(float),
                     'Close':x.c.to_numpy(float),'Volume':x.v.to_numpy(float)},index=pd.DatetimeIndex(timestamps))
    if start:df=df[df.index.date>=date.fromisoformat(start)]
    if end:df=df[df.index.date<=date.fromisoformat(end)]
    if df.empty:raise ValueError('No cached bars within requested dates')
    return df,{'source':'Local historical cache: '+', '.join(sorted(set(x.source))),
               'realtime':False,'last_bar':df.index[-1].isoformat(),
               'notice':'Only actual cached bars; missing dates are not synthesized.'}
