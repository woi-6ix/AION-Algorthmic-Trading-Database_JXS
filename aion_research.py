"""Read-only AION research endpoints, intentionally separate from execution.

This module is NOT an execution market-data feed. No API credentials or broker
accounts are consulted. Public research data is unlicensed for commercial use.
"""
from __future__ import annotations

import math
import os
import re
import threading
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from fastapi import APIRouter, HTTPException, Query

router = APIRouter(prefix="/api/research", tags=["research"])
CACHE: dict[tuple, tuple[float, Any]] = {}
CACHE_LOCK = threading.Lock()
SYMBOL_RE = re.compile(r"^[A-Z][A-Z0-9.\-^]{0,9}$")
DEFAULT_TICKERS = "SPY,QQQ,IWM,DIA,AAPL,MSFT,NVDA,TSLA,AMZN,META"
MAX_TICKERS = 12
PROVIDER = os.getenv("AION_RESEARCH_PROVIDER", "yfinance").strip().lower()
DISCLAIMER = (
    "Research-only indicative EOD data; not live option quotes, not broker-verified, "
    "not suitable for order execution. Yahoo/yfinance content is intended for personal use; "
    "obtain a licensed data feed for commercial deployment."
)


def _cache(key: tuple, ttl: int, fn):
    with CACHE_LOCK:
        item = CACHE.get(key)
        if item and time.monotonic() - item[0] < ttl:
            return item[1]
    value = fn()
    with CACHE_LOCK:
        CACHE[key] = (time.monotonic(), value)
    return value


def _validate_symbol(symbol: str) -> str:
    symbol = symbol.strip().upper()
    if not SYMBOL_RE.fullmatch(symbol):
        raise HTTPException(status_code=400, detail="Invalid ticker symbol")
    return symbol


def parse_symbols(raw: str) -> list[str]:
    symbols = []
    for segment in raw.split(","):
        s = _validate_symbol(segment)
        if s not in symbols:
            symbols.append(s)
        if len(symbols) > MAX_TICKERS:
            raise HTTPException(status_code=400, detail=f"At most {MAX_TICKERS} tickers")
    if not symbols:
        raise HTTPException(status_code=400, detail="Enter at least one ticker")
    return symbols


def _history(symbol: str, period: str = "1y"):
    if PROVIDER != "yfinance":
        raise RuntimeError("Research provider disabled. Set AION_RESEARCH_PROVIDER=yfinance for personal research testing.")
    def load():
        try:
            import yfinance as yf
        except ImportError as exc:
            raise RuntimeError("Missing optional package. Install requirements-aion-research.txt") from exc
        bars = yf.Ticker(symbol).history(period=period, interval="1d", auto_adjust=False)
        if bars is None or bars.empty:
            raise RuntimeError("No market bars returned for this ticker")
        bars = bars.dropna(subset=["Close"])
        if len(bars) < 35:
            raise RuntimeError("Insufficient daily bars for the indicators")
        return bars
    return _cache(("history", PROVIDER, symbol, period), 600, load)


def _finite(value, digits=2):
    try:
        n = float(value)
        return round(n, digits) if math.isfinite(n) else None
    except (TypeError, ValueError):
        return None


def _rsi(close, length=14):
    delta = close.diff()
    up = delta.clip(lower=0).ewm(alpha=1/length, adjust=False).mean()
    down = (-delta.clip(upper=0)).ewm(alpha=1/length, adjust=False).mean()
    u, d = float(up.iloc[-1]), float(down.iloc[-1])
    if d == 0:
        return 100.0 if u > 0 else 50.0
    return 100 - (100 / (1 + u / d))


def _metrics(symbol, bars):
    import numpy as np
    c = bars["Close"].astype(float)
    v = bars["Volume"].astype(float) if "Volume" in bars else None
    if len(c) < 35: raise RuntimeError("Insufficient bars")
    last, prev = float(c.iloc[-1]), float(c.iloc[-2])
    ma20, ma50 = float(c.ewm(span=20, adjust=False).mean().iloc[-1]), float(c.ewm(span=50, adjust=False).mean().iloc[-1])
    rsi = _rsi(c)
    ret = c.pct_change().dropna()
    vol = float(ret.tail(20).std() * np.sqrt(252) * 100) if len(ret) >= 20 else None
    rvol = (float(v.iloc[-1]) / float(v.iloc[-21:-1].mean())) if v is not None and len(v)>21 and v.iloc[-21:-1].mean()>0 else None
    trend = "BULLISH" if last>ma20>ma50 else "BEARISH" if last<ma20<ma50 else "MIXED"
    return {"symbol":symbol,"last":_finite(last),"date":bars.index[-1].date().isoformat(),
            "change_pct":_finite((last/prev-1)*100) if prev else None,
            "rsi_14":_finite(rsi,1),"ema20":_finite(ma20),"ema50":_finite(ma50),
            "rvol":_finite(rvol),"realized_vol_20d_pct":_finite(vol),"trend":trend,
            "bars": len(bars)}


def _summary_error(exc):
    # Avoid leaking underlying provider URL, query strings, API secrets or stack traces.
    return type(exc).__name__ + ": market data unavailable"


@router.get("/screener")
def screener(tickers: str = Query(default=DEFAULT_TICKERS, max_length=130)):
    symbols = parse_symbols(tickers)
    def scan_one(s):
        try:
            return _metrics(s, _history(s, "3mo")), None
        except Exception as exc:
            return None, {"symbol":s,"message":_summary_error(exc)}
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(scan_one, symbols))
    return {"generated_at":datetime.now(timezone.utc).isoformat(),
            "provider":PROVIDER,"frequency":"daily close / delayed or stale",
            "universe":"user-selected watchlist, not entire US market",
            "rows":[row for row,err in results if row],
            "errors":[err for row,err in results if err],
            "notice":DISCLAIMER}


@router.get("/chart")
def chart(symbol: str = "SPY"):
    symbol = _validate_symbol(symbol)
    try:
        bars = _history(symbol, "3mo")
        return {"symbol":symbol,"date":bars.index[-1].date().isoformat(),
                "series":[{"date":d.date().isoformat(),"close":_finite(p,3)} for d,p in zip(bars.index[-75:],bars["Close"].iloc[-75:])],
                "metrics":_metrics(symbol,bars),"provider":PROVIDER,"notice":DISCLAIMER}
    except Exception as exc:
        raise HTTPException(status_code=503,detail=_summary_error(exc)) from exc


def _vader(text):
    try:
        from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
        return SentimentIntensityAnalyzer().polarity_scores(text)["compound"]
    except ImportError:
        return None


def _get_news(symbol):
    # Fixed host and whitelisted symbol, never fetch arbitrary user URL.
    query = f'"{symbol}" stock market ETF finance'
    url = "https://news.google.com/rss/search?" + urlencode({"q":query,"hl":"en-US","gl":"US","ceid":"US:en"})
    req = Request(url, headers={"User-Agent":"AION-Research-Monitor/1.0"})
    with urlopen(req, timeout=8) as r:
        raw = r.read(180_000)
    root = ET.fromstring(raw)
    items=[]
    for x in root.findall("./channel/item")[:15]:
        title=(x.findtext("title") or "").strip()[:240]
        link=(x.findtext("link") or "").strip()
        source=(x.findtext("source") or "").strip()[:90]
        when=(x.findtext("pubDate") or "").strip()
        if not title or not link.startswith("https://"):
            continue
        score=_vader(title)
        items.append({"title":title,"url":link,"source":source,"published":when,
                      "sentiment":_finite(score,3) if score is not None else None,
                      "label":"Positive" if score is not None and score>=.2 else "Negative" if score is not None and score<=-.2 else "Neutral" if score is not None else "Unavailable"})
    return items


@router.get("/sentiment")
def sentiment(symbol: str = "SPY"):
    symbol = _validate_symbol(symbol)
    try:
        items=_cache(("news",symbol),600,lambda:_get_news(symbol))
    except Exception as exc:
        raise HTTPException(status_code=503,detail="News feed unavailable ("+type(exc).__name__+")") from exc
    scores=[x["sentiment"] for x in items if x["sentiment"] is not None]
    avg=round(sum(scores)/len(scores),3) if scores else None
    return {"symbol":symbol,"headlines":items,"headline_count":len(items),
            "average_compound":avg,"label":"Positive" if avg is not None and avg>=.2 else "Negative" if avg is not None and avg<=-.2 else "Neutral" if avg is not None else "Unavailable",
            "method":"VADER lexical scoring of headlines; not FinBERT, not the original HG sentiment model",
            "feed":"Google News RSS search (headline and publisher)","notice":"Indicative, keyword-dependent, not licensed for commercial news redistribution. Not trading advice."}


def _hmm_result(bars):
    try:
        import numpy as np
        from hmmlearn.hmm import GaussianHMM
    except ImportError:
        return {"state":"Unavailable", "method":"hmmlearn not installed", "confidence":None}
    c=bars["Close"].astype(float)
    rets=(c.pct_change()*100).dropna()
    realized=rets.rolling(5).std().dropna()
    rets=rets.reindex(realized.index)
    if len(rets)<90: return {"state":"Insufficient history","method":"GaussianHMM"}
    feats=np.column_stack([rets.to_numpy(), realized.to_numpy()])
    stdev=feats.std(axis=0)
    feats=(feats-feats.mean(axis=0))/np.maximum(stdev,1e-6)
    try:
        import warnings
        model=GaussianHMM(n_components=3,covariance_type="diag",n_iter=80,tol=.005,random_state=42)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            model.fit(feats)
            probs=model.predict_proba(feats[-1:])[0]
            current=int(probs.argmax())
            assigned=model.predict(feats)
            state_returns=[]
            for i in range(3):
                mask=assigned==i
                state_returns.append((float(rets.to_numpy()[mask].mean()) if mask.sum()>1 else 0,
                                      float(realized.to_numpy()[mask].mean()) if mask.sum()>1 else 0))
            mean_ret,mean_vol=state_returns[current]
            overall_vol=float(realized.mean())
            label="HIGH VOLATILITY" if mean_vol>overall_vol*1.25 else "BULL BIAS" if mean_ret>0 else "BEAR BIAS" if mean_ret<0 else "SIDEWAYS"
            return {"state":label,"confidence":_finite(float(probs[current])*100,1),
                    "method":"3-state GaussianHMM fitted on daily returns + 5-day realized volatility",
                    "warning":"Unsupervised statistical states; sensitive to training and not predictive probabilities of profitable trades"}
    except Exception:
        return {"state":"Fit unavailable","confidence":None,"method":"GaussianHMM failed to converge"}


def _garch_result(bars):
    try:
        import numpy as np
        from arch import arch_model
    except ImportError:
        return {"annualized_pct":None,"method":"arch not installed"}
    rets=(bars["Close"].astype(float).pct_change()*100).dropna()
    if len(rets)<110:
        return {"annualized_pct":None,"method":"Insufficient daily history for GARCH"}
    try:
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            res=arch_model(rets,mean="Constant",vol="GARCH",p=1,q=1,dist="normal",rescale=False).fit(disp="off",show_warning=False)
            var=float(res.forecast(horizon=1,reindex=False).variance.iloc[-1,0])
        return {"annualized_pct":_finite(math.sqrt(max(0,var)*252),1),
                "method":"GARCH(1,1) next-day conditional volatility, annualized; daily close data"}
    except Exception:
        return {"annualized_pct":None,"method":"GARCH model fit unavailable"}


def _storm_proxy(bars):
    import numpy as np
    c=bars["Close"].astype(float)
    r=c.pct_change().dropna()
    momentum=float(c.iloc[-1]/c.iloc[-21]-1) if len(c)>21 else 0
    vol20=float(r.iloc[-20:].std())
    vhistory=r.rolling(20).std().dropna()
    vmed=float(vhistory.tail(100).median()) if len(vhistory)>1 else vol20
    stress=vol20>vmed*1.45
    label="STRESS / ELEVATED VOL" if stress else "UPTREND" if momentum>0.01 else "DOWNTREND" if momentum<-.01 else "RANGE"
    return {"state":label,"momentum_20d_pct":_finite(momentum*100),"vol_ratio":_finite(vol20/vmed if vmed else 1),
            "method":"STORM-style heuristic proxy: 20-day momentum and realized-volatility ratio",
            "warning":"This is NOT the original custom STORM model; original source code needed to port it exactly"}


@router.get("/regime")
def regime(symbol: str = "SPY"):
    symbol = _validate_symbol(symbol)
    try:
        bars=_history(symbol,"1y")
        return {"symbol":symbol,"as_of":bars.index[-1].date().isoformat(),"timeframe":"daily (not 3-minute)",
                "hmm":_hmm_result(bars),"garch":_garch_result(bars),"storm":_storm_proxy(bars),
                "provider":PROVIDER,"notice":DISCLAIMER+" HMM/GARCH are optional local research fits; STORM is a proxy."}
    except Exception as exc:
        raise HTTPException(status_code=503,detail=_summary_error(exc)) from exc


@router.get("/terminal")
def original_terminal():
    # Local port probe only. Do not proxy or expose other endpoints/credentials.
    url="http://127.0.0.1:8000/"
    try:
        with urlopen(Request(url, headers={"User-Agent":"AION-Monitor/1.0"}), timeout=1.2) as r:
            response=int(r.status)
        online=response<500
    except Exception:
        online=False
    return {"online":online,"local_port":8000,
            "notice":"Original backtester runs separately. To open it on a phone, configure a second private Tailscale Serve HTTPS port; no backtest algorithms are duplicated here."}


@router.get("/capabilities")
def capabilities():
    try: import yfinance; stock_data=True
    except ImportError: stock_data=False
    try: import vaderSentiment; headline_scoring=True
    except ImportError: headline_scoring=False
    try: import hmmlearn; hmm=True
    except ImportError: hmm=False
    try: import arch; garch=True
    except ImportError: garch=False
    return {"provider":PROVIDER,"price_data_installed":stock_data,"headline_scoring_installed":headline_scoring,
            "hmm_installed":hmm,"garch_installed":garch,"live_options_quote_feed":False,
            "read_only":True,"backtest_bridge":"link to existing port 8000", "notice":DISCLAIMER}
