"""AION V2 research execution engine.

KAIROS_V5_CORE_PORT follows the user-authored Pine V5 kernel-flip, ATR slope,
volatility-push and ATR/R bracket formulas with independent Python computations.
It does NOT reproduce jdehorty's imported functions, LC neighbor classifier,
walk-forward Pine HMM or TradingView order-fill behavior. Results are explicitly
labeled an unvalidated CORE PORT, not full V5 or SPY option performance.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
import re
import numpy as np
import pandas as pd

NY = 'America/New_York'
INTERVAL_MINUTES = {'1m':1,'2m':2,'3m':3,'5m':5,'15m':15,'30m':30,'1h':60,'1d':390,'1wk':1950}

@dataclass
class KairosParams:
    lookback: int = 8
    relative_weight: float = 8.0
    regression_level: int = 25
    lag: int = 6
    smoothing: bool = True
    use_vp: bool = True
    vp_window: int = 10
    use_slope: bool = False
    slope_bars: int = 2
    min_slope_atr: float = 0.05
    use_ema: bool = False
    use_sma: bool = False
    ema_len: int = 200
    sma_len: int = 200
    atr_multiple: float = 1.5
    reward_r: float = 0.4
    notional_usd: float = 2500.0
    fees_per_side: float = 1.30
    slippage_bps: float = 1.0
    exit_opposite: bool = True
    cutoff: str = '15:00'

    @classmethod
    def from_dict(cls, val):
        val = val or {}
        if not isinstance(val,dict): raise ValueError('parameters must be an object')
        p = cls(**{k:v for k,v in val.items() if k in cls.__dataclass_fields__})
        if not (3<=int(p.lookback)<=100 and 2<=int(p.regression_level)<=100 and 1<=int(p.lag)<=20):
            raise ValueError('Invalid kernel parameters')
        if not (0.1<=float(p.reward_r)<=8 and .05<=float(p.atr_multiple)<=20 and 100<=float(p.notional_usd)<=1_000_000):
            raise ValueError('Invalid stop, target, or notional')
        if p.cutoff not in ('15:00','15:30','15:55'): raise ValueError('Invalid session cutoff')
        return p


def wilder_atr(df,n):
    prev=df.Close.shift(1)
    tr=pd.concat([df.High-df.Low,(df.High-prev).abs(),(df.Low-prev).abs()],axis=1).max(axis=1)
    return tr.ewm(alpha=1/n,adjust=False,min_periods=n).mean()


def kernel_estimate(closes,lookback=8,relative_weight=8.,regression_level=25,kind='rq'):
    """Trailing kernel estimate; weighting shape matches jdehorty's published kernels.
    Lookback to imported KernelFunctions start-bar details still needs parity testing.
    """
    size=lookback+regression_level+1
    j=np.arange(size,dtype=float)
    w=(1+j*j/(2*lookback*lookback*relative_weight))**(-relative_weight) if kind=='rq' else np.exp(-0.5*j*j/(lookback*lookback))
    w=w/sum(w)
    out=np.full(len(closes),np.nan)
    for i in range(size-1,len(closes)):
        out[i]=float(np.dot(closes[i-size+1:i+1][::-1],w))
    return out


def kairos_v5_signals(df,params=None):
    p=KairosParams.from_dict(params)
    if len(df)<max(100, p.regression_level+p.lookback+20):
        raise ValueError('KAIROS core port needs at least 100 historical bars')
    x=df.copy().sort_index()
    if x.index.tz is None: raise ValueError('OHLCV timestamps must be timezone-aware')
    x.index=x.index.tz_convert(NY)
    close=x.Close.to_numpy(dtype=float);open_=x.Open.to_numpy(dtype=float)
    high=x.High.to_numpy(dtype=float);low=x.Low.to_numpy(dtype=float)
    # Notice: a manual replacement for kernel functions; not a verified Pine port.
    rq=kernel_estimate(close,p.lookback,p.relative_weight,p.regression_level,'rq')
    gauss=kernel_estimate(close,max(1,p.lookback-p.lag),p.relative_weight,p.regression_level,'gauss')
    bull=(gauss>=rq) if p.smoothing else (rq>np.roll(rq,1))
    bear=(gauss<=rq) if p.smoothing else (rq<np.roll(rq,1))
    bull[~np.isfinite(gauss+rq)]=False;bear[~np.isfinite(gauss+rq)]=False
    flip_up=bull & ~np.roll(bull,1);flip_down=bear & ~np.roll(bear,1)
    flip_up[0]=False;flip_down[0]=False
    atr14=wilder_atr(x,14).to_numpy()
    atr5=wilder_atr(x,5).to_numpy();atr20=wilder_atr(x,20).to_numpy()
    vol20=x.Volume.rolling(20,min_periods=20).mean().to_numpy()
    range_=np.maximum(high-low,.01);body=np.abs(close-open_)
    with np.errstate(divide='ignore',invalid='ignore'):
        ratio=atr5/atr20; ra=range_/atr20;vr=x.Volume.to_numpy()/vol20
        b_a=body/atr20;b_r=body/range_;loc=(close-low)/range_
    score=(ratio>=1.10).astype(int)+(ra>=1.25).astype(int)+(vr>=1.50).astype(int)
    bull_vp=(score>=2)&(close>open_)&(loc>=.70)&(b_a>=.80)&(b_r>=.65)
    bear_vp=(score>=2)&(close<open_)&(loc<=.30)&(b_a>=.80)&(b_r>=.65)
    em=x.Close.ewm(span=p.ema_len,adjust=False,min_periods=p.ema_len).mean().to_numpy()
    sm=x.Close.rolling(p.sma_len,min_periods=p.sma_len).mean().to_numpy()
    age_b=age_s=100000
    long=np.zeros(len(x),dtype=bool);short=np.zeros(len(x),dtype=bool)
    for i in range(1,len(x)):
        age_b=0 if bull_vp[i] else age_b+1
        age_s=0 if bear_vp[i] else age_s+1
        tt=x.index[i]+pd.Timedelta(minutes=INTERVAL_MINUTES.get(str(x.attrs.get('interval','5m')),5))
        hhmm=tt.strftime('%H:%M')
        session='09:30'<=hhmm<p.cutoff
        slope=rq[i]-rq[i-p.slope_bars] if i>=p.slope_bars else np.nan
        slope=slope/atr14[i] if atr14[i]>0 else np.nan
        gate_long=(not p.use_slope or slope>=p.min_slope_atr) and (not p.use_vp or(age_b<=p.vp_window and age_b<age_s))
        gate_short=(not p.use_slope or slope<=-p.min_slope_atr) and (not p.use_vp or(age_s<=p.vp_window and age_s<age_b))
        long[i]=bool(session and flip_up[i] and gate_long and (not p.use_ema or close[i]>em[i]) and (not p.use_sma or close[i]>sm[i]))
        short[i]=bool(session and flip_down[i] and gate_short and (not p.use_ema or close[i]<em[i]) and (not p.use_sma or close[i]<sm[i]))
    sig=x[['Open','High','Low','Close','Volume']].copy()
    sig['atr']=atr14;sig['rq_kernel']=rq;sig['gaussian_kernel']=gauss
    sig['bull_flip']=flip_up;sig['bear_flip']=flip_down
    sig['long_entry']=long;sig['short_entry']=short
    return sig


def shares_from_signals(frame,signals,params=None,interval='5m'):
    """Signals are calculated from historical bars. Execute at signal-bar CLOSE to
    approximate Pine's process_orders_on_close=true, with positive slippage and
    conservative stop-first bar-high/low exit. No intrabar sequencing claims.
    """
    p=KairosParams.from_dict(params)
    df=frame.sort_index().copy()
    if df.index.tz is None:raise ValueError('Timezone-aware bars required')
    df.index=df.index.tz_convert(NY)
    sig=signals.reindex(df.index)
    duration=pd.Timedelta(minutes=INTERVAL_MINUTES.get(interval,5))
    trades=[];position=None
    def settle(i,price,reason):
        nonlocal position
        direction=position['direction'];ep=position['entry_price'];q=position['qty']
        fill=price*(1-p.slippage_bps/10000*direction)
        pnl=(fill-ep)*direction*q-2*p.fees_per_side
        trades.append({'date':(df.index[i]+duration).isoformat(),'entry_at':position['entry_at'],
                       'exit_at':(df.index[i]+duration).isoformat(),'side':'LONG' if direction==1 else 'SHORT',
                       'direction':direction,'entry_price':round(ep,4),'exit_price':round(fill,4),
                       'qty':q,'pnl':round(pnl,4),'exit_reason':reason,
                       'underlying_entry_price':round(position['close'],4)})
        position=None
    for i in range(len(df)):
        bar=df.iloc[i];ts=df.index[i]+duration;clock=ts.strftime('%H:%M')
        if position is not None:
            d=position['direction'];st=position['stop'];tg=position['target'];ep=position['entry_price']
            # Adverse opening gap first, then intrabar bar-extrema bracket checks.
            hit_stop=(bar.Open<=st if d==1 else bar.Open>=st)
            if hit_stop: settle(i,float(bar.Open),'gap stop')
            elif (bar.Low<=st if d==1 else bar.High>=st): settle(i,float(st),'stop (conservative first)')
            elif (bar.High>=tg if d==1 else bar.Low<=tg): settle(i,float(tg),'target')
            elif clock>=p.cutoff or (i+1<len(df) and df.index[i+1].date()!=df.index[i].date()):
                settle(i,float(bar.Close),'session close')
            elif p.exit_opposite and bool(sig.iloc[i]['bear_flip'] if d==1 else sig.iloc[i]['bull_flip']):
                settle(i,float(bar.Close),'opposite kernel flip')
        if i<len(df)-1 and position is None and '09:30'<=clock<p.cutoff and np.isfinite(sig.iloc[i]['atr']):
            d=1 if bool(sig.iloc[i]['long_entry']) else -1 if bool(sig.iloc[i]['short_entry']) else 0
            if d:
                raw_ep=float(bar.Close)
                ep=raw_ep*(1+p.slippage_bps/10000*d)
                q=int(p.notional_usd//ep)
                if q<1:continue
                distance=float(sig.iloc[i]['atr'])*p.atr_multiple
                position={'entry_price':ep,'entry_at':ts.isoformat(),'direction':d,'qty':q,
                          'stop':ep-d*distance,'target':ep+d*distance*p.reward_r,'close':raw_ep}
    if position:
        settle(len(df)-1,float(df.iloc[-1].Close),'end of available data')
    return trades


def signals_from_csv(content):
    """Exact signal replay interface: each row is an actual TradingView-derived
    ENTRY and EXIT pair, as opposed to guessed signals from prices.
    Required entry_at, exit_at, side, underlying_entry_price columns.
    """
    import io
    if len(content)>8_000_000:raise ValueError('Signal CSV exceeds 8 MB')
    df=pd.read_csv(io.BytesIO(content))
    fields=['entry_at','exit_at','side','underlying_entry_price']
    if any(f not in df for f in fields):raise ValueError('Signal CSV needs entry_at, exit_at, side, underlying_entry_price columns')
    if len(df)>10000:raise ValueError('10,000 signals maximum')
    out=[]
    for _,r in df.iterrows():
        enter=pd.Timestamp(r.entry_at);leave=pd.Timestamp(r.exit_at)
        if enter.tz is None or leave.tz is None: raise ValueError('Signal timestamps must include timezone offset')
        enter=enter.tz_convert(NY);leave=leave.tz_convert(NY)
        side=str(r.side).upper().strip()
        if side not in ('LONG','SHORT') or leave<=enter or enter.date()!=leave.date():raise ValueError('Only same-day LONG/SHORT signals with exit after entry are valid')
        if not 9<=enter.hour<=16 or leave.hour>16:raise ValueError('Signals must be regular-session timestamps')
        px=float(r.underlying_entry_price)
        if not math.isfinite(px) or px<=0:raise ValueError('Invalid underlying entry price')
        item={'date':leave.isoformat(),'entry_at':enter.isoformat(),'exit_at':leave.isoformat(),
              'side':side,'underlying_entry_price':px,'direction':1 if side=='LONG' else -1}
        if 'contract' in df and pd.notna(r.get('contract')) and str(r.get('contract')).strip():
            from options_v2 import validate_contract
            contract=str(r.get('contract')).strip().upper()
            validate_contract(contract,enter.date(),direction=item['direction'])
            item['contract']=contract
        out.append(item)
    if not out:raise ValueError('No valid signals')
    return out
