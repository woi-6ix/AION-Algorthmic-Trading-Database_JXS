"""AION Quant Terminal — independent research dashboard.

TradingView MCP in ChatGPT does not grant this web process TradingView credentials.
The running app uses a Yahoo Finance fallback and accepts user-imported TradingView
Strategy Tester trade exports. Optional externally authorized MCP sync is in tv_bridge.py.
"""
from __future__ import annotations

import csv
import io
import json
import math
import os
import re
import secrets
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from fastapi import FastAPI, HTTPException, UploadFile, File, Form, Query, Header, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

ROOT = Path(__file__).resolve().parent
# Local-only provider configuration; never return credentials in API responses.
# The key is optional. TradingView MCP from ChatGPT does not supply it.
local_env = ROOT / 'aion-local.env'
if local_env.is_file():
    try:
        for raw_line in local_env.read_text(encoding='utf-8-sig').splitlines():
            raw_line = raw_line.strip()
            if raw_line.startswith('AION_MASSIVE_API_KEY=') and not os.getenv('AION_MASSIVE_API_KEY'):
                os.environ['AION_MASSIVE_API_KEY'] = raw_line.split('=',1)[1].strip().strip('"')
    except OSError:
        pass
DB_PATH = Path(os.getenv('AION_DB', str(ROOT / 'data' / 'aion.sqlite3')))
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

app = FastAPI(title='AION Quant Terminal', version='0.2.0')
app.mount('/static', StaticFiles(directory=str(ROOT / 'static')), name='static')

ALGORITHMS = [
    {'id':'KAIROS_V5','name':'KAIROS LC V5','family':'KAIROS','status':'import','description':'Exact Pine backtests must be imported from TradingView.'},
    {'id':'KAIROS_V4_1','name':'KAIROS LC V4.1','family':'KAIROS','status':'import','description':'Exact Pine backtests must be imported from TradingView.'},
    {'id':'KAIROS_V4','name':'KAIROS LC V4','family':'KAIROS','status':'import','description':'Exact Pine backtests must be imported from TradingView.'},
    {'id':'DELPHI_3','name':'DELPHI 3','family':'DELPHI','status':'import','description':'Exact Pine backtests must be imported from TradingView.'},
    {'id':'DELPHI_5','name':'DELPHI 5','family':'DELPHI','status':'import','description':'Exact Pine backtests must be imported from TradingView.'},
    {'id':'BELLEROPHON_ORB','name':'BELLEROPHON ORB','family':'BELLEROPHON','status':'import','description':'Exact Pine backtests must be imported from TradingView.'},
    {'id':'KAIROS_V5_CORE_PORT','name':'KAIROS V5 · Core Python Port','family':'KAIROS','status':'runnable-core','description':'From actual V5 Pine: kernel flip, Volatility Push, slope, ATR/R exits and session. LC/HMM are NOT yet ported or Pine-verified.'},
    {'id':'NW_FLIP_RESEARCH','name':'NW Flip · research','family':'RESEARCH','status':'runnable','description':'Independent trailing Gaussian-kernel baseline — NOT your Pine implementation.'},
    {'id':'ORB_RESEARCH','name':'ORB · research','family':'RESEARCH','status':'runnable','description':'Independent opening-range baseline — NOT your Pine implementation.'},
]
INTERVALS = {'1m','2m','3m','5m','15m','30m','1h','1d','1wk'}
PERIODS = {'5d','1mo','3mo','6mo','1y','2y','5y'}


def connection():
    db = sqlite3.connect(DB_PATH, timeout=15)
    db.row_factory = sqlite3.Row
    db.execute('''CREATE TABLE IF NOT EXISTS backtests (
        id INTEGER PRIMARY KEY AUTOINCREMENT, label TEXT NOT NULL, algorithm TEXT NOT NULL,
        symbol TEXT NOT NULL, timeframe TEXT NOT NULL, source TEXT NOT NULL,
        created_at TEXT NOT NULL, trades_json TEXT NOT NULL, metrics_json TEXT NOT NULL,
        start_date TEXT, end_date TEXT)''')
    db.execute("""CREATE TABLE IF NOT EXISTS tv_snapshots (
        symbol TEXT NOT NULL, interval TEXT NOT NULL, received_at REAL NOT NULL,
        payload_json TEXT NOT NULL, PRIMARY KEY(symbol,interval))""")
    db.commit()
    return db


def clean_symbol(symbol: str):
    symbol = symbol.strip().upper()
    if not re.fullmatch(r'[A-Z0-9.\^=\-]{1,18}', symbol):
        raise HTTPException(422, 'Ticker must be a symbol such as SPY, QQQ, AAPL, or BTC-USD.')
    return symbol


def finite(value, default=None):
    try:
        value = float(value)
        return round(value, 6) if math.isfinite(value) else default
    except (ValueError, TypeError):
        return default


def fetch_bars(symbol: str, interval='5m', period='1mo') -> tuple[pd.DataFrame, dict]:
    if interval not in INTERVALS or period not in PERIODS:
        raise HTTPException(422, 'Unsupported timeframe or period.')
    # Yahoo's intraday history window is limited; do not silently fabricate missing bars.
    intraday = interval not in {'1d', '1wk'}
    days = {'5d':5, '1mo':30, '3mo':90, '6mo':180, '1y':365, '2y':730, '5y':1825}[period]
    cap = 7 if interval in {'1m', '3m'} else 60 if interval in {'2m','5m','15m','30m'} else 730
    if intraday and days > cap:
        raise HTTPException(422, f'{interval} Yahoo data cannot reliably cover {period} (limit ~{cap} days). Use an external historical feed for deeper backtests.')
    try:
        import yfinance as yf
    except ImportError:
        raise HTTPException(503, 'yfinance is not installed. Run: pip install -r requirements.txt')
    data_interval = '1m' if interval == '3m' else interval
    try:
        frame = yf.Ticker(symbol).history(period=period, interval=data_interval, auto_adjust=False, prepost=False, timeout=18)
    except Exception as e:
        raise HTTPException(502, f'Market data provider failed: {str(e)[:180]}')
    if frame is None or frame.empty:
        raise HTTPException(404, f'No market data found for {symbol} ({interval}, {period}).')
    frame = frame[['Open','High','Low','Close','Volume']].dropna(subset=['Open','High','Low','Close']).copy()
    if interval == '3m':
        # Right-closed bars with NY market-hour anchor; empty buckets discarded.
        local = frame.tz_convert('America/New_York') if frame.index.tz is not None else frame
        frame = local.resample('3min', origin='start_day', offset='0min').agg({
            'Open':'first','High':'max','Low':'min','Close':'last','Volume':'sum'
        }).dropna(subset=['Open','Close'])
    return frame, {'source':'Yahoo Finance (independent fallback)','realtime':False,
                   'notice':'May be delayed; TradingView ChatGPT authorization is not shared with this website.',
                   'last_bar':frame.index[-1].isoformat()}


def bars_json(df: pd.DataFrame, max_points=900):
    res = []
    for timestamp, bar in df.tail(max_points).iterrows():
        res.append({'t':timestamp.isoformat(), 'o':finite(bar['Open']), 'h':finite(bar['High']),
                    'l':finite(bar['Low']), 'c':finite(bar['Close']), 'v':finite(bar['Volume'])})
    return res


def news_and_sentiment(symbol):
    try:
        import yfinance as yf
        from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
        analyser = SentimentIntensityAnalyzer()
        items = yf.Ticker(symbol).news or []
        headlines=[]
        for item in items[:25]:
            x = item.get('content') or item
            title = x.get('title') or item.get('title')
            if not title: continue
            s = float(analyser.polarity_scores(title)['compound'])
            link = (x.get('canonicalUrl') or x.get('clickThroughUrl') or {})
            url = link.get('url','') if isinstance(link,dict) else str(link)
            if not url: url = item.get('link','')
            provider = x.get('provider') or {}
            headlines.append({'title':title,'url':url if str(url).startswith('http') else '',
                              'publisher':provider.get('displayName','') if isinstance(provider,dict) else str(provider),
                              'published':x.get('pubDate') or x.get('displayTime') or '',
                              'score':round(s,3),'tone':'positive' if s > .15 else 'negative' if s < -.15 else 'neutral'})
        mean = float(np.mean([h['score'] for h in headlines])) if headlines else 0.0
        return headlines[:12], {'model':'VADER headline-only baseline (HG prototype)',
             'score':round(mean*100,1) if headlines else None,
             'tone':('Bullish' if mean > .15 else 'Bearish' if mean < -.15 else 'Neutral') if headlines else 'Unavailable',
             'confidence':'Low — headline-only' if len(headlines)<8 else 'Indicative — headline-only',
             'count':len(headlines), 'note':'Not the proprietary HG model; provide HG methodology to replace.'}
    except Exception as e:
        return [], {'model':'HG placeholder','score':None,'tone':'Unavailable','count':0,
                    'note':f'News or sentiment provider unavailable: {str(e)[:110]}'}


def compute_regime(df:pd.DataFrame):
    closes = df.Close.astype(float)
    if len(closes) < 130:
        return {'status':'insufficient','note':'At least 130 candles needed to fit a 3-state HMM/GARCH model.'}
    returns = closes.pct_change().dropna()*100
    rv = returns.rolling(20).std().bfill()
    features = np.column_stack([returns.values,rv.values])
    mean=features.mean(axis=0)
    std=np.maximum(features.std(axis=0),1e-8)
    x=(features-mean)/std
    hmm_result = None
    garch_result = None
    try:
        from hmmlearn.hmm import GaussianHMM
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter('ignore')
            model = GaussianHMM(n_components=3,covariance_type='diag',n_iter=120,
                                random_state=23,min_covar=1e-3)
            model.fit(x)
            states = model.predict(x)
            probs = model.predict_proba(x)[-1]
        volatility_by_state = [float(np.mean(rv.values[states==i])) if (states==i).any() else float('inf') for i in range(3)]
        low,mid,high = np.argsort(volatility_by_state)
        pretty = ['Calm','Normal','High volatility']
        sorted_probs = [float(probs[low]),float(probs[mid]),float(probs[high])]
        latest = pretty[int(np.argmax(sorted_probs))]
        hmm_result = {'label':latest,'probabilities':dict(zip(pretty,[round(p*100,1) for p in sorted_probs])),
                      'confidence':round(max(probs)*100,1),
                      'method':'3-state Gaussian HMM on bar returns and 20-bar realized volatility'}
    except Exception as e:
        hmm_result = {'label':'Unavailable','error':str(e)[:125]}
    try:
        from arch import arch_model
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter('ignore')
            # arch optimizes on percent returns, forecasts next bar variance
            model=arch_model(returns.values,vol='Garch',p=1,q=1,mean='Constant',dist='normal',rescale=False)
            fitted=model.fit(disp='off',show_warning=False)
            sigma=math.sqrt(float(fitted.forecast(horizon=1).variance.values[-1,0]))
            hist=returns.rolling(20).std().dropna().values
            pct=float(np.mean(hist <= sigma)*100)
            garch_result={'sigma_pct_per_bar':round(sigma,4),'vol_percentile':round(pct,1),
                          'method':'GARCH(1,1), 1-bar-ahead conditional sigma'}
    except Exception as e:
        garch_result={'error':str(e)[:125]}
    if 'probabilities' in hmm_result and 'vol_percentile' in garch_result:
        risk=.6*hmm_result['probabilities']['High volatility']+.4*garch_result['vol_percentile']
        storm={'score':round(risk,1),'label':'STORM' if risk>=67 else 'NORMAL' if risk>=33 else 'CALM',
               'formula':'0.60 × HMM(high-vol probability) + 0.40 × GARCH volatility percentile',
               'note':'Custom research composite, not a verified proprietary STORM indicator. Not a directional trade signal.'}
    else:
        storm={'score':None,'label':'Unavailable','note':'Requires fitted HMM and GARCH.'}
    return {'status':'computed','hmm':hmm_result,'garch':garch_result,'storm':storm,
            'asof':df.index[-1].isoformat()}


def trade_metrics(trades):
    p = [float(t['pnl']) for t in trades]
    if not p: return None
    cum = np.cumsum(p)
    peak = np.maximum.accumulate(np.insert(cum,0,0))
    equity = np.insert(cum,0,0)
    drawdown = peak-equity
    wins=[v for v in p if v>0]; losses=[v for v in p if v<0]
    profit_factor=(sum(wins)/abs(sum(losses))) if losses else None
    return {'net_pnl':round(sum(p),2),'trades':len(p),'wins':len(wins),
        'win_rate':round(100*len(wins)/len(p),1),'profit_factor':round(profit_factor,2) if profit_factor is not None else None,
        'max_drawdown_usd':round(float(max(drawdown)),2), 'avg_trade':round(float(np.mean(p)),2),
        'avg_win':round(float(np.mean(wins)),2) if wins else 0,
        'avg_loss':round(float(np.mean(losses)),2) if losses else 0,
        'gross_profit':round(sum(wins),2),'gross_loss':round(sum(losses),2),
        'equity':[{'n':i,'pnl':round(float(v),2)} for i,v in enumerate(cum,1)]}


def parse_num(value):
    if isinstance(value,(int,float)):return float(value)
    txt = str(value or '').replace(',','').replace('$','').replace('%','').replace('−','-').strip()
    if txt.startswith('(') and txt.endswith(')'): txt='-'+txt[1:-1]
    return float(txt) if txt else 0.0


def csv_to_trades(content:bytes):
    try:
        text=content.decode('utf-8-sig')
    except UnicodeDecodeError: raise HTTPException(422, 'CSV must use UTF-8 encoding.')
    reader=csv.DictReader(io.StringIO(text))
    if not reader.fieldnames: raise HTTPException(422,'CSV has no header row.')
    rows=list(reader)
    if len(rows)>150000: raise HTTPException(413,'Maximum 150,000 CSV rows per upload.')
    normalized=[{re.sub(r'[^a-z0-9]','',k.lower()):v for k,v in row.items() if k} for row in rows]
    def pick(row,*keys):
        for key in keys:
            if key in row and str(row[key]).strip(): return row[key]
        return None
    direct=[]
    for row in normalized:
        v=pick(row,'pnl','netpnl','netpnlusd','netpl','netplussd','netplusd','profit','profitusd','profitloss','profitlossusd')
        if v is None: continue
        try: pnl=parse_num(v)
        except ValueError: continue
        if not math.isfinite(pnl):continue
        typ=str(pick(row,'type') or '').lower()
        if 'entry' in typ or ('exit' not in typ and pick(row,'tradenumber','trade') and 'entry' in str(pick(row,'signal') or '').lower()):
            continue
        direct.append({'date':str(pick(row,'date','datetime','dateandtime','exittime','time') or ''),
                       'side':str(pick(row,'side','type','direction') or ''),
                       'pnl':round(pnl,4)})
    if not direct: raise HTTPException(422, 'No trade P&L rows found. Include a pnl column (USD), or export TradingView List of Trades with Net P&L USD.')
    # TradingView exports may contain both an entry and exit row. Keep exit-only rows when type distinguishes them.
    if any('exit' in str(r.get('type','')).lower() for r in normalized):
        only_exit=[]
        for row in normalized:
            if 'exit' not in str(row.get('type','')).lower():continue
            value=pick(row,'pnl','netpnl','netpnlusd','netpl','netplussd','netplusd','profit','profitusd','profitloss','profitlossusd')
            if value is None:continue
            try: val=parse_num(value)
            except ValueError:continue
            if math.isfinite(val):
                only_exit.append({'date':str(pick(row,'dateandtime','date','datetime','time') or ''),
                                  'side':str(pick(row,'type') or ''),'pnl':round(val,4)})
        if only_exit: direct=only_exit
    return direct


def save_run(label,algorithm,symbol,timeframe,source,trades):
    metrics=trade_metrics(trades)
    if not metrics: raise HTTPException(422,'No closed trades to store.')
    dates=[t.get('date','') for t in trades if t.get('date')]
    start,end=(min(dates),max(dates)) if dates else (None,None)
    with connection() as db:
        cur=db.execute('''INSERT INTO backtests
          (label,algorithm,symbol,timeframe,source,created_at,trades_json,metrics_json,start_date,end_date)
          VALUES (?,?,?,?,?,?,?,?,?,?)''', (label,algorithm,symbol,timeframe,source,
          datetime.now(timezone.utc).isoformat(),json.dumps(trades),json.dumps(metrics),start,end))
        db.commit()
        id=cur.lastrowid
    return {'id':id,'label':label,'algorithm':algorithm,'symbol':symbol,'timeframe':timeframe,
            'source':source,'metrics':metrics,'start_date':start,'end_date':end}


def list_runs():
    with connection() as db:
        rows=db.execute('''SELECT id,label,algorithm,symbol,timeframe,source,created_at,metrics_json,start_date,end_date
                           FROM backtests ORDER BY id DESC LIMIT 100''').fetchall()
    return [{**{k:r[k] for k in r.keys() if k!='metrics_json'},'metrics':json.loads(r['metrics_json'])} for r in rows]


def research_backtest(df,algorithm,risk_usd=500,fees_per_trade=1.30,slippage_bps=1.0):
    """Two distinct educational baselines, not the user's exact scripts.
    Bar-close signal enters next bar open; no same-bar lookahead entry.
    Stops/targets are checked using bar highs/lows with conservative stop-first priority.
    """
    if len(df)<75:raise HTTPException(422,'At least 75 candles required for the research baseline.')
    closes=df.Close.values.astype(float); opens=df.Open.values.astype(float)
    highs=df.High.values.astype(float); lows=df.Low.values.astype(float)
    prev=np.roll(closes,1); prev[0]=closes[0]
    tr=np.maximum(highs-lows,np.maximum(abs(highs-prev),abs(lows-prev)))
    atr=pd.Series(tr).rolling(14).mean().values
    k=np.arange(0,24)
    weights=np.exp(-0.5*(k/7)**2)
    weights/=sum(weights)
    nw=np.full(len(df),np.nan)
    for i in range(23,len(df)):
        nw[i]=sum(closes[i-j]*weights[j] for j in range(24))
    trades=[]; pending=0; position=None
    for i in range(31,len(df)):
        t=df.index[i]
        dt=t.tz_convert('America/New_York') if hasattr(t,'tz_convert') and t.tzinfo else t
        hhmm=dt.strftime('%H:%M')
        # Never carry a position overnight. At the last regular-hours bar, close it.
        if position:
            exit_price=None
            if position['direction']==1:
                if lows[i]<=position['stop']:exit_price=position['stop']
                elif highs[i]>=position['target']:exit_price=position['target']
            else:
                if highs[i]>=position['stop']:exit_price=position['stop']
                elif lows[i]<=position['target']:exit_price=position['target']
            if exit_price is None and (hhmm>='15:00' or (i+1<len(df) and df.index[i+1].date()!=t.date())):
                exit_price=closes[i]
            if exit_price is not None:
                # Round-trip slippage and two-side commission included.
                ep=position['entry']; direction=position['direction']; qty=position['qty']
                pl=(exit_price-ep)*direction*qty - fees_per_trade*2 - (ep+exit_price)*qty*slippage_bps/10000
                trades.append({'date':t.isoformat(),'side':'LONG' if direction==1 else 'SHORT','pnl':round(pl,4),
                               'entry_price':round(ep,4),'exit_price':round(float(exit_price),4),'qty':qty})
                position=None
        if position is None and pending:
            atrv=max(float(atr[i-1]),0.01)
            ep=opens[i]
            qty=max(1,int(risk_usd/max(1.5*atrv,0.01)))
            stop=ep-pending*1.5*atrv
            target=ep+pending*1.5*atrv
            position={'entry':float(ep),'direction':pending,'stop':float(stop),'target':float(target),'qty':qty}
            pending=0
        if position or pending or not ('09:30'<=hhmm<'14:45'):
            continue
        if algorithm=='NW_FLIP_RESEARCH':
            if all(np.isfinite(nw[i-j]) for j in [0,1,2]):
                prev_slope=nw[i-1]-nw[i-2]; slope=nw[i]-nw[i-1]
                pending=1 if prev_slope<=0 and slope>0 else -1 if prev_slope>=0 and slope<0 else 0
        else:
            if not ('10:00'<=hhmm<'11:00'): continue
            daymask=np.array([z.date()==t.date() and z.hour==9 and z.minute<35 for z in df.index])
            if not np.any(daymask):continue
            hi=np.max(highs[daymask]); lo=np.min(lows[daymask]); body=abs(closes[i]-opens[i])
            past=np.max(abs(closes[max(0,i-10):i]-opens[max(0,i-10):i]))
            pending=1 if closes[i]>hi and body>past else -1 if closes[i]<lo and body>past else 0
    if position:
        ep=position['entry']; x=closes[-1]; d=position['direction']; qty=position['qty']
        pnl=(x-ep)*d*qty-fees_per_trade*2-(ep+x)*qty*slippage_bps/10000
        trades.append({'date':df.index[-1].isoformat(),'side':'LONG' if d==1 else 'SHORT','pnl':round(pnl,4)})
    return trades


def normalize_tv_bars(value):
    """Accept TradingView MCP OHLCV columns or rows and normalize for chart use."""
    source = value.get('data',value) if isinstance(value,dict) else value
    bars = source.get('bars',source) if isinstance(source,dict) else source
    if isinstance(bars,dict) and 't' in bars and isinstance(bars['t'],list):
        keys=['t','o','h','l','c','v']
        if not all(k in bars for k in keys):raise HTTPException(422,'Missing OHLCV arrays.')
        size=len(bars['t'])
        if not all(len(bars[k])==size for k in keys):raise HTTPException(422,'OHLCV column sizes do not match.')
        bars=[dict(zip(keys,[bars[k][i] for k in keys])) for i in range(size)]
    if not isinstance(bars,list) or not 2<=len(bars)<=8000:
        raise HTTPException(422,'TradingView sync requires 2–8000 OHLCV bars.')
    cleaned=[]
    for b in bars:
        try:
            t=b.get('t')
            t=int(t) if isinstance(t,(int,float)) else pd.Timestamp(t).timestamp()
            o,h,l,c=[float(b[k]) for k in ('o','h','l','c')]
            v=float(b.get('v',0) or 0)
            if not all(math.isfinite(x) for x in [t,o,h,l,c,v]) or h<l or o<=0 or c<=0:raise ValueError()
        except (ValueError,TypeError,KeyError):
            raise HTTPException(422,'Malformed or non-finite OHLCV bar.')
        cleaned.append({'t':int(t),'o':o,'h':h,'l':l,'c':c,'v':v})
    return sorted({b['t']:b for b in cleaned}.values(),key=lambda x:x['t'])


def tv_snapshot(symbol,interval):
    with connection() as db:
        row=db.execute('SELECT received_at,payload_json FROM tv_snapshots WHERE symbol=? AND interval=?',(symbol,interval)).fetchone()
    if not row or time.time()-row['received_at'] > 240:return None
    return json.loads(row['payload_json']),row['received_at']


def frame_from_tv(bars):
    df=pd.DataFrame(bars)
    df.index=pd.to_datetime(df.pop('t'),unit='s',utc=True).dt.tz_convert('America/New_York')
    return df.rename(columns={'o':'Open','h':'High','l':'Low','c':'Close','v':'Volume'})


def tv_headline_sentiment(data):
    news=data.get('data',data) if isinstance(data,dict) else {}
    items=news.get('headlines',[]) if isinstance(news,dict) else []
    headlines=[]
    try:
        from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
        analyzer=SentimentIntensityAnalyzer()
    except ImportError:analyzer=None
    for h in items[:25]:
        title=str(h.get('title') or '')
        if not title:continue
        p=h.get('provider') or {}
        provider=p.get('name','TradingView') if isinstance(p,dict) else str(p)
        score=analyzer.polarity_scores(title)['compound'] if analyzer else 0
        t=h.get('published')
        date=datetime.fromtimestamp(t,timezone.utc).isoformat() if isinstance(t,(float,int)) else (t or '')
        link=h.get('link','')
        headlines.append({'title':title,'url':link if isinstance(link,str) and link.startswith('https://') else '',
                          'publisher':provider,'published':date,'score':round(score,3),
                          'tone':'positive' if score>.15 else 'negative' if score<-.15 else 'neutral'})
    scores=[n['score'] for n in headlines]
    mean=sum(scores)/len(scores) if scores else 0
    return headlines[:12], {'model':'VADER headline-only HG prototype','score':round(mean*100,1) if scores and analyzer else None,
        'tone':('Bullish' if mean>.15 else 'Bearish' if mean<-.15 else 'Neutral') if scores and analyzer else 'Unavailable',
        'count':len(scores),'confidence':'Indicative — headline only' if len(scores)>=8 else 'Low — headline only',
        'note':'TradingView news. Not a verified proprietary HG model.'}


@app.post('/api/tv-sync')
async def tv_sync(request:Request, x_aion_sync_token: str | None = Header(default=None)):
    """Authorized external worker pushes TradingView observations; no ChatGPT token reuse."""
    token=os.getenv('AION_TV_SYNC_TOKEN','')
    if not token:raise HTTPException(503,'TV sync is disabled; set AION_TV_SYNC_TOKEN on the server.')
    if not x_aion_sync_token or not secrets.compare_digest(x_aion_sync_token,token):
        raise HTTPException(401,'Invalid sync token.')
    body=await request.body()
    if len(body)>3500000:raise HTTPException(413,'Maximum 3.5 MB per TradingView snapshot.')
    try:payload=json.loads(body)
    except ValueError:raise HTTPException(422,'Invalid JSON payload.')
    if not isinstance(payload,dict):raise HTTPException(422,'A JSON object is required.')
    symbol=clean_symbol(str(payload.get('symbol','')))
    interval=str(payload.get('interval',''))
    if interval not in INTERVALS:raise HTTPException(422,'Unsupported interval.')
    bars=normalize_tv_bars(payload.get('ohlcv',payload.get('bars')))
    snapshot={'bars':bars,'news':payload.get('news',{})}
    with connection() as db:
        db.execute("""INSERT INTO tv_snapshots(symbol,interval,received_at,payload_json)
                      VALUES(?,?,?,?) ON CONFLICT(symbol,interval) DO UPDATE SET
                      received_at=excluded.received_at,payload_json=excluded.payload_json""",
                   (symbol,interval,time.time(),json.dumps(snapshot)))
        db.commit()
    return {'ok':True,'symbol':symbol,'interval':interval,'bars':len(bars),'source':'TradingView MCP sync'}


@app.get('/')
def root():return FileResponse(ROOT/'static'/'index.html')

@app.get('/api/health')
def health():return {'ok':True,'version':'0.2.0','data_source':'Yahoo fallback; separately authenticated TradingView MCP sync available'}

@app.get('/api/algorithms')
def algorithms():return ALGORITHMS

@app.get('/api/overview')
def overview(symbol: str='SPY',interval:str='5m',period:str='1mo'):
    symbol=clean_symbol(symbol)
    if interval not in INTERVALS or period not in PERIODS:
        raise HTTPException(422, 'Unsupported timeframe or period.')
    cached=tv_snapshot(symbol,interval)
    if cached:
        snapshot,received=cached
        frame=frame_from_tv(snapshot['bars'])
        start=pd.Timestamp.now(tz='UTC')-pd.Timedelta(days={'5d':5,'1mo':30,'3mo':90,'6mo':180,'1y':365,'2y':730,'5y':1825}[period])
        clipped=frame[frame.index>=start]
        if len(clipped)>=2:frame=clipped
        source={'source':'TradingView MCP (separately authenticated sync)','realtime':False,
                'notice':'MCP observations may be delayed. Synced history may not cover full requested lookback.',
                'last_bar':frame.index[-1].isoformat(),'synced_at':datetime.fromtimestamp(received,timezone.utc).isoformat()}
        news,sent=tv_headline_sentiment(snapshot.get('news',{}))
    else:
        frame,source=fetch_bars(symbol,interval,period)
        news,sent=news_and_sentiment(symbol)
    latest=frame.iloc[-1];prev=frame.iloc[-2] if len(frame)>1 else latest
    regime=compute_regime(frame)
    return {'symbol':symbol,'interval':interval,'period':period,'source':source,
            'quote':{'price':finite(latest.Close),'change_pct':finite((latest.Close/prev.Close-1)*100,0),
                     'volume':finite(latest.Volume),'high':finite(latest.High),'low':finite(latest.Low)},
            'bars':bars_json(frame),'regime':regime,'sentiment':sent,'news':news}

@app.get('/api/backtests')
def backtests():return list_runs()

@app.post('/api/backtests/import')
async def import_backtest(file:UploadFile=File(...),algorithm:str=Form(...),
                          symbol:str=Form(...),timeframe:str=Form('5m'),label:str=Form('')):
    if algorithm not in {a['id'] for a in ALGORITHMS}:
        raise HTTPException(422,'Unknown algorithm.')
    if timeframe not in INTERVALS:raise HTTPException(422,'Unsupported timeframe.')
    data=await file.read(4*1024*1024+1)
    if len(data)>4*1024*1024:raise HTTPException(413,'CSV exceeds 4 MB.')
    trades=csv_to_trades(data)
    return save_run(label or f'{algorithm} · {symbol.upper()} · {timeframe}',algorithm,
                    clean_symbol(symbol),timeframe,'TradingView / CSV import',trades)

@app.post('/api/backtests/research')
def run_research(symbol:str='SPY',interval:str='5m',period:str='5d',
                 algorithm:str='NW_FLIP_RESEARCH',risk_usd:float=500):
    if algorithm not in {'NW_FLIP_RESEARCH','ORB_RESEARCH'}:
        raise HTTPException(422,'Exact KAIROS/DELPHI Pine backtests are not available via TradingView MCP. Import a real Strategy Tester export.')
    if not (20<=risk_usd<=10000): raise HTTPException(422,'risk_usd must be between 20 and 10,000.')
    df,_=fetch_bars(clean_symbol(symbol),interval,period)
    trades=research_backtest(df,algorithm,risk_usd)
    if not trades:
        return {'status':'no_trades','note':'No baseline signals fired for this period. No performance was fabricated.'}
    run=save_run(f'{algorithm} · {symbol.upper()} · {interval}',algorithm,clean_symbol(symbol),interval,
                 'Independent research baseline · Yahoo data',trades)
    return {'status':'ok','run':run,'warning':'NOT a replication of KAIROS, DELPHI, or TradingView fills; bar-based share P&L, not 0DTE option P&L.'}

@app.get('/api/backtests/{run_id}')
def backtest_detail(run_id:int):
    with connection() as db: row=db.execute('SELECT * FROM backtests WHERE id=?',(run_id,)).fetchone()
    if not row:raise HTTPException(404,'Backtest not found')
    x=dict(row);x['trades']=json.loads(x.pop('trades_json'));x['metrics']=json.loads(x.pop('metrics_json'))
    return x

@app.get('/api/trades-template.csv')
def csv_template():
    return Response(content='date,side,pnl\n2026-10-01 09:45,LONG,125.25\n2026-10-01 10:30,SHORT,-45.50\n',
        media_type='text/csv',headers={'Content-Disposition':'attachment; filename=aion-trades-template.csv'})


# ---------------------------------------------------------------------------
# V2 DATA / STOCK-BAR / 0DTE QUOTE RESEARCH ROUTES
# ---------------------------------------------------------------------------
from .market_v2 import csv_bars, massive_stock_history, store_bars, load_bars
from .engine_v2 import KairosParams, kairos_v5_signals, shares_from_signals, signals_from_csv
from .options_v2 import csv_quotes, store_quotes, quote_table, replay_0dte


def v2_problem(e):
    if isinstance(e,HTTPException):raise e
    raise HTTPException(422,str(e)[:600])


def load_data_for_backtest(symbol,interval,period,provider='auto',start=None,end=None):
    if interval not in INTERVALS or period not in PERIODS:
        raise HTTPException(422,'Unknown interval or period')
    if provider not in ('auto','cache','yahoo','massive','tradingview_sync'):
        raise HTTPException(422,'Provider must be auto, cache, yahoo, massive or tradingview_sync')
    if (start is None)!=(end is None):raise HTTPException(422,'Supply both start and end dates')
    if start and end:
        from datetime import date
        try:
            a=datetime.strptime(start,'%Y-%m-%d').date();b=datetime.strptime(end,'%Y-%m-%d').date()
        except ValueError:raise HTTPException(422,'Use YYYY-MM-DD dates')
        if b<a or (b-a).days>3650:raise HTTPException(422,'Invalid or excessively wide historical date range')
    if provider=='cache':
        with connection() as db:return load_bars(db,symbol,interval,start,end)
    if provider=='tradingview_sync':
        snap=tv_snapshot(symbol,interval)
        if not snap:raise HTTPException(404,'No fresh TradingView MCP snapshot. ChatGPT connection does not authenticate the website.')
        frame=frame_from_tv(snap[0]['bars']);meta={'source':'TradingView separately authorized sync','notice':'Only available snapshot bars. Validate historical window.','realtime':False,'last_bar':frame.index[-1].isoformat()}
    elif provider=='massive' or provider=='auto' and os.getenv('AION_MASSIVE_API_KEY',''):
        if not start or not end:
            today=datetime.now(timezone.utc).date()
            days={'5d':5,'1mo':30,'3mo':90,'6mo':180,'1y':365,'2y':730,'5y':1825}[period]
            start=str(today-pd.Timedelta(days=days));end=str(today)
            a=datetime.strptime(start,'%Y-%m-%d').date();b=datetime.strptime(end,'%Y-%m-%d').date()
        try:frame,meta=massive_stock_history(symbol,interval,start,end)
        except ValueError as e:raise HTTPException(502,str(e))
    elif provider in ('auto','yahoo'):
        if start:
            days=(b-a).days+1
            cap=7 if interval in {'1m','3m'} else 60 if interval in {'2m','5m','15m','30m'} else 730 if interval=='1h' else 9999
            if days>cap:
                raise HTTPException(422,f'Yahoo {interval} history normally limited to ~{cap} days. Configure AION_MASSIVE_API_KEY or import CSV for a longer period.')
            try:
                import yfinance as yf
                yf_interval='1m' if interval=='3m' else interval
                frame=yf.Ticker(symbol).history(start=start,end=str(b+pd.Timedelta(days=1)),interval=yf_interval,auto_adjust=False,prepost=False,timeout=25)
            except Exception as e:raise HTTPException(502,'Yahoo history request failed: '+str(e)[:170])
            if frame is None or frame.empty:raise HTTPException(404,'No Yahoo bars for selected dates and interval')
            frame=frame[['Open','High','Low','Close','Volume']].dropna(subset=['Open','High','Low','Close'])
            if interval=='3m':
                frame=frame.tz_convert('America/New_York').resample('3min',origin='start_day').agg({'Open':'first','High':'max','Low':'min','Close':'last','Volume':'sum'}).dropna(subset=['Open','Close'])
            meta={'source':'Yahoo Finance historical fallback','notice':'Intraday history is limited; market prices may be delayed.','realtime':False,'last_bar':frame.index[-1].isoformat()}
        else:frame,meta=fetch_bars(symbol,interval,period)
    if start:frame=frame[(frame.index.date>=a)&(frame.index.date<=b)]
    if frame.empty:raise HTTPException(404,'No bars in the selected date range')
    frame=frame.sort_index()
    with connection() as db:store_bars(db,frame,symbol,interval,meta['source'])
    return frame,meta


def v2_data_report(df,meta):
    return {'count':len(df),'first_bar':df.index[0].isoformat(),'last_bar':df.index[-1].isoformat(),
            'source':meta.get('source'),'notice':meta.get('notice',''),
            'warning':'Availability is the actual returned bars, not a guarantee that every expected session is present.'}


@app.get('/api/data/status')
def data_status(symbol:str='SPY',interval:str='3m'):
    symbol=clean_symbol(symbol)
    if interval not in INTERVALS:raise HTTPException(422,'Invalid interval')
    with connection() as db:
        db.execute('''CREATE TABLE IF NOT EXISTS historical_bars(symbol TEXT,interval TEXT,t TEXT,
            o REAL,h REAL,l REAL,c REAL,v REAL,source TEXT,PRIMARY KEY(symbol,interval,t))''')
        q=db.execute('SELECT COUNT(*) n,MIN(t) first,MAX(t) last FROM historical_bars WHERE symbol=? AND interval=?',(symbol,interval)).fetchone()
        quote_table(db)
        qn=db.execute('SELECT COUNT(DISTINCT contract) FROM option_quotes WHERE contract LIKE ?',(f'O:{symbol}%',)).fetchone()[0]
    return {'symbol':symbol,'interval':interval,'bars':q['n'],'first_bar':q['first'],
            'last_bar':q['last'],'option_contracts_cached':qn,'massive_api_configured':bool(os.getenv('AION_MASSIVE_API_KEY')),
            'tradingview_auth':'Not transferable from ChatGPT; use authorized sync endpoint'}


@app.post('/api/data/download')
async def data_download(request:Request):
    body=await request.json()
    symbol=clean_symbol(str(body.get('symbol','SPY')))
    try:
        df,meta=load_data_for_backtest(symbol,body.get('interval','3m'),body.get('period','5d'),
                 body.get('provider','auto'),body.get('start') or None,body.get('end') or None)
        return v2_data_report(df,meta)
    except Exception as e:v2_problem(e)


@app.post('/api/data/import')
async def data_import(file:UploadFile=File(...),symbol:str=Form('SPY'),interval:str=Form('3m'),tz:str=Form('America/New_York')):
    symbol=clean_symbol(symbol)
    raw=await file.read(40_000_001)
    try:
        df=csv_bars(raw,interval,tz)
        with connection() as db:store_bars(db,df,symbol,interval,'User imported OHLCV CSV')
        return v2_data_report(df,{'source':'User imported OHLCV CSV'})
    except Exception as e:v2_problem(e)


@app.get('/api/data/template.csv')
def data_template():
    content='timestamp,open,high,low,close,volume\n2026-10-01T09:30:00-04:00,700.0,700.5,699.7,700.2,10000\n2026-10-01T09:33:00-04:00,700.2,700.8,700.0,700.6,12000\n'
    return Response(content=content,media_type='text/csv',headers={'Content-Disposition':'attachment;filename=aion-ohlcv-template.csv'})


@app.post('/api/strategy/run')
async def stock_strategy_run(request:Request):
    body=await request.json();algo=body.get('algorithm','KAIROS_V5_CORE_PORT')
    if algo!='KAIROS_V5_CORE_PORT':raise HTTPException(422,'Only KAIROS V5 CORE PORT is executable; exact Pine KAIROS, DELPHI and BELLEROPHON need imported signals or trades.')
    symbol=clean_symbol(body.get('symbol','SPY'));interval=body.get('interval','3m');period=body.get('period','5d')
    if interval in ('1d','1wk'):raise HTTPException(422,'KAIROS V5 core is intraday-only. Choose 1m–1h bars; daily and weekly OHLCV can still be imported for other analyses.')
    provider=body.get('provider','auto')
    try:
        df,meta=load_data_for_backtest(symbol,interval,period,provider,body.get('start') or None,body.get('end') or None)
        df.attrs['interval']=interval
        params=KairosParams.from_dict(body.get('parameters'))
        sig=kairos_v5_signals(df,body.get('parameters'))
        trades=shares_from_signals(df,sig,body.get('parameters'),interval)
    except Exception as e:v2_problem(e)
    details=v2_data_report(df,meta)
    caveat='Unvalidated KAIROS V5 CORE PORT, not exact Pine: LC/ML, LC environment and Pine walk-forward HMM omitted; imported jdehorty kernels have not been parity-tested. BAR-CLOSE stock share fills; no option premiums.'
    if not trades:return {'status':'no_trades','coverage':details,'warning':caveat,'note':'No core-port entries fired. Never fabricate P&L.'}
    run=save_run(f'KAIROS V5 core · {symbol} · {interval}',algo,symbol,interval,
                 'KAIROS V5 CORE PORT / '+meta['source'],trades)
    return {'status':'ok','run':run,'coverage':details,'warning':caveat,
            'diagnostics':{'entry_signals':int(sig.long_entry.sum()+sig.short_entry.sum()),
                           'kernel_flips':int(sig.bull_flip.sum()+sig.bear_flip.sum())}}


@app.post('/api/signals/import')
async def signals_import(file:UploadFile=File(...),symbol:str=Form('SPY'),algorithm:str=Form('KAIROS_V5'),label:str=Form('')):
    symbol=clean_symbol(symbol)
    if algorithm not in {a['id'] for a in ALGORITHMS}:raise HTTPException(422,'Unknown algorithm')
    try:signals=signals_from_csv(await file.read(8_000_001))
    except Exception as e:v2_problem(e)
    with connection() as db:
        db.execute('''CREATE TABLE IF NOT EXISTS signal_sets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,symbol TEXT,algorithm TEXT,label TEXT,
            created_at TEXT,signals_json TEXT)''')
        cur=db.execute('INSERT INTO signal_sets(symbol,algorithm,label,created_at,signals_json) VALUES(?,?,?,?,?)',
                       (symbol,algorithm,label or f'{algorithm} verified signal timestamps',
                        datetime.now(timezone.utc).isoformat(),json.dumps(signals)))
        db.commit();sid=cur.lastrowid
    return {'id':sid,'rows':len(signals),'symbol':symbol,'algorithm':algorithm,
            'note':'Source-specific signals are reused as timestamps/direction; option P&L still requires actual NBBO quotes.'}


@app.get('/api/signals')
def signal_list():
    with connection() as db:
        db.execute('''CREATE TABLE IF NOT EXISTS signal_sets (id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT,algorithm TEXT,label TEXT,created_at TEXT,signals_json TEXT)''')
        rows=db.execute('SELECT id,symbol,algorithm,label,created_at FROM signal_sets ORDER BY id DESC LIMIT 100').fetchall()
    return [dict(x) for x in rows]


@app.get('/api/signals/template.csv')
def signals_template():
    return Response(content='entry_at,exit_at,side,underlying_entry_price,contract\n2026-10-01T10:00:00-04:00,2026-10-01T10:15:00-04:00,LONG,700.5,O:SPY261001C00700500\n',
      media_type='text/csv',headers={'Content-Disposition':'attachment;filename=aion-signal-template.csv'})


@app.post('/api/options/quotes/import')
async def options_quotes_import(file:UploadFile=File(...)):
    try:
        q=csv_quotes(await file.read(40_000_001))
        with connection() as db:stored=store_quotes(db,q,'User historical bid/ask CSV')
        return {'ok':True,'quotes':stored,'contracts':q.contract.nunique(),'earliest_ms':int(q.t_ms.min()),'latest_ms':int(q.t_ms.max())}
    except Exception as e:v2_problem(e)


@app.get('/api/options/quotes/template.csv')
def option_quotes_template():
    return Response(content='timestamp,contract,bid,ask\n2026-10-01T10:00:00-04:00,O:SPY261001C00700000,1.25,1.30\n2026-10-01T10:15:00-04:00,O:SPY261001C00700000,1.55,1.61\n',
      media_type='text/csv',headers={'Content-Disposition':'attachment;filename=aion-options-quotes-template.csv'})


@app.post('/api/options/run')
async def options_run(request:Request):
    body=await request.json();symbol=clean_symbol(str(body.get('symbol','SPY')))
    if symbol not in {'SPY','QQQ','IWM'}:raise HTTPException(422,'0DTE automatic ATM contract selection currently supports SPY, QQQ and IWM')
    source=body.get('source','stock_run')
    with connection() as db:
        if source=='stock_run':
            run_id=int(body.get('run_id',0))
            run=db.execute('SELECT * FROM backtests WHERE id=?',(run_id,)).fetchone()
            if not run:raise HTTPException(404,'Run not found; run a stock-bar backtest first')
            if run['symbol']!=symbol:raise HTTPException(422,'Run symbol differs from selected symbol')
            if run['algorithm']!='KAIROS_V5_CORE_PORT' or not run['source'].startswith('KAIROS V5 CORE PORT'):
                raise HTTPException(422,'Only generated core-port stock runs have validated entry/exit timestamps. Import explicit TradingView signals for other algorithms.')
            candidates=json.loads(run['trades_json']);algo=run['algorithm'];timeframe=run['timeframe']
        elif source=='signal_set':
            sig=db.execute('SELECT * FROM signal_sets WHERE id=?',(int(body.get('signal_set_id',0)),)).fetchone()
            if not sig:raise HTTPException(404,'Signal set not found')
            if sig['symbol']!=symbol:raise HTTPException(422,'Signal set symbol differs from selected symbol')
            candidates=json.loads(sig['signals_json']);algo=sig['algorithm'];timeframe='signal timestamps'
        else:raise HTTPException(422,'source must be stock_run or signal_set')
        if not candidates:raise HTTPException(422,'Source has no entry/exit signals')
        try:
            trades,coverage=replay_0dte(db,candidates,symbol,contracts=body.get('contracts','atm'),
              strike_step=float(body.get('strike_step',.5)),contracts_qty=int(body.get('qty',1)),
              fee_per_contract=float(body.get('fee',.65)),
              slippage_per_contract=float(body.get('slippage',.01)),
              max_quote_delay_s=int(body.get('max_quote_delay_s',60)),
              auto_download=bool(body.get('auto_download',False)),
              exit_mode=str(body.get('exit_mode','signal')),
              premium_stop_pct=float(body.get('premium_stop_pct',.25)),
              premium_reward_r=float(body.get('premium_reward_r',1.0)))
        except Exception as e:v2_problem(e)
    warning='REAL NBBO QUOTE REPLAY, not options Strategy Tester: actual ask entry/bid exit, selectable signal exit or observed option-premium bracket. Missing quotes skip trades; sampled NBBO and delayed fills cannot guarantee execution. Coverage is essential.'
    if not trades:return {'status':'no_trades','coverage':coverage,'warning':warning,'note':'No valid historical option bid/ask entry-and-exit pairs. Supply quotes or configure a provider with quote-history entitlement.'}
    run=save_run(f'0DTE QUOTE REPLAY · {algo} · {symbol}',algo,symbol,timeframe,
                 '0DTE bid/ask quote replay (not Pine options)',trades)
    return {'status':'ok','run':run,'coverage':coverage,'warning':warning}

@app.exception_handler(Exception)
async def generic_error(request,exc):
    # Operational errors do not trigger synthetic demo numbers.
    return JSONResponse(status_code=500,content={'detail':f'Unexpected application error: {str(exc)[:160]}'})
