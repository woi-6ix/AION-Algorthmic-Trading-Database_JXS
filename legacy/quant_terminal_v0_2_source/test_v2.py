import sys
import io
import json
from pathlib import Path
import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import server
import market_v2
from engine_v2 import kairos_v5_signals, shares_from_signals, signals_from_csv
from options_v2 import make_0dte_contract, csv_quotes, store_quotes, replay_0dte


def test_ohlcv_import_and_cache(tmp_path):
    raw=b'timestamp,open,high,low,close,volume\n2026-10-01T09:30:00-04:00,700,701,699,700.2,100\n2026-10-01T09:33:00-04:00,700.2,701,700,700.6,80\n'
    frame=market_v2.csv_bars(raw,'3m')
    db=server.sqlite3.connect(tmp_path/'a.sqlite3');db.row_factory=server.sqlite3.Row
    assert market_v2.store_bars(db,frame,'SPY','3m','user import')==2
    f,meta=market_v2.load_bars(db,'SPY','3m')
    assert len(f)==2 and f.iloc[-1].Close==700.6
    assert 'user import' in meta['source']
    with pytest.raises(ValueError):market_v2.csv_bars(raw.replace(b'701,699',b'699,701'),'3m')


def test_csv_rejects_futureless_or_invalid():
    raw=b'timestamp,contract,bid,ask\n2026-10-01T10:00:00-04:00,O:SPY261001C00700500,1.25,1.30\n'
    assert len(csv_quotes(raw))==1
    with pytest.raises(ValueError):csv_quotes(raw.replace(b'1.25,1.30',b'1.50,1.30'))
    with pytest.raises(ValueError):csv_quotes(raw.replace(b'2026-10-01T10:00:00-04:00',b'2026-10-01 10:00:00'))


def test_true_nbbo_spread_fee_and_missing_quote(tmp_path):
    db=server.sqlite3.connect(tmp_path/'opts.sqlite3');db.row_factory=server.sqlite3.Row
    contract=make_0dte_contract('SPY','2026-10-01T10:00:00-04:00',1,700.5)
    assert contract=='O:SPY261001C00700500'
    csv=(f'timestamp,contract,bid,ask\n2026-10-01T10:00:01-04:00,{contract},1.25,1.30\n'
         f'2026-10-01T10:15:01-04:00,{contract},1.55,1.61\n').encode()
    store_quotes(db,csv_quotes(csv))
    signal={'entry_at':'2026-10-01T10:00:00-04:00','exit_at':'2026-10-01T10:15:00-04:00',
            'side':'LONG','underlying_entry_price':700.5}
    trades,coverage=replay_0dte(db,[signal],'SPY',contracts_qty=2)
    # 2 contracts * (bid_exit - ask_entry - $0.02 slippage) *100 - 4 * 0.65 = $43.40
    assert coverage['filled']==1 and coverage['skipped']==0
    assert trades[0]['contract']==contract
    assert trades[0]['pnl']==43.4
    miss=dict(signal,entry_at='2026-10-01T11:00:00-04:00',exit_at='2026-10-01T11:15:00-04:00')
    trades,coverage=replay_0dte(db,[miss],'SPY')
    assert not trades and coverage['skipped']==1


def synthetic_intraday():
    ix=[]
    for day in ['2026-10-01','2026-10-02','2026-10-05','2026-10-06']:
        ix.extend(pd.date_range(day+' 09:30',periods=111,freq='3min',tz='America/New_York'))
    rng=np.random.default_rng(155)
    close=700+2.8*np.sin(np.arange(len(ix))/18)+np.arange(len(ix))*.02
    open_=np.r_[close[0],close[:-1]]
    return pd.DataFrame({'Open':open_,'High':np.maximum(open_,close)+.2,'Low':np.minimum(open_,close)-.2,
                         'Close':close,'Volume':20000+np.round(rng.uniform(0,10000,len(ix)))},index=pd.DatetimeIndex(ix))


def test_kairos_core_references_real_pine_structure():
    df=synthetic_intraday();df.attrs['interval']='3m'
    signals=kairos_v5_signals(df,{'use_vp':False,'smoothing':False})
    assert set(['rq_kernel','gaussian_kernel','bull_flip','long_entry','atr']).issubset(signals.columns)
    assert signals['bull_flip'].sum()>0
    assert (signals.long_entry | signals.short_entry).sum()>0
    trades=shares_from_signals(df,signals,{'use_vp':False,'smoothing':False},'3m')
    assert len(trades)>0
    assert all(pd.Timestamp(x['entry_at']).date()==pd.Timestamp(x['exit_at']).date() for x in trades)
    assert all(x['qty']>=1 and np.isfinite(x['pnl']) for x in trades)


def test_signal_csv_strict():
    good=b'entry_at,exit_at,side,underlying_entry_price\n2026-10-01T10:00:00-04:00,2026-10-01T10:15:00-04:00,LONG,700.5\n'
    assert len(signals_from_csv(good))==1
    with pytest.raises(ValueError):signals_from_csv(good.replace(b'2026-10-01T10:15:00-04:00',b'2026-10-02T10:15:00-04:00'))


def test_v2_endpoints_upload_and_option_replay(tmp_path,monkeypatch):
    monkeypatch.setattr(server,'DB_PATH',tmp_path/'api.sqlite3')
    client=TestClient(server.app)
    assert client.get('/api/health').json()['version']=='0.2.0'
    rows=b'timestamp,open,high,low,close,volume\n2026-10-01T09:30:00-04:00,700,701,699,700.2,100\n2026-10-01T09:33:00-04:00,700.2,701,700,700.6,80\n'
    x=client.post('/api/data/import',data={'symbol':'SPY','interval':'3m'},files={'file':('bars.csv',rows,'text/csv')})
    assert x.status_code==200,x.text
    assert client.get('/api/data/status?symbol=SPY&interval=3m').json()['bars']==2
    assert client.get('/api/data/template.csv').status_code==200
    sig=b'entry_at,exit_at,side,underlying_entry_price\n2026-10-01T10:00:00-04:00,2026-10-01T10:15:00-04:00,LONG,700.5\n'
    x=client.post('/api/signals/import',data={'symbol':'SPY','algorithm':'KAIROS_V5'},files={'file':('signals.csv',sig,'text/csv')})
    assert x.status_code==200,x.text
    signal_id=x.json()['id']
    quote=b'timestamp,contract,bid,ask\n2026-10-01T10:00:01-04:00,O:SPY261001C00700500,1.25,1.30\n2026-10-01T10:15:01-04:00,O:SPY261001C00700500,1.55,1.61\n'
    x=client.post('/api/options/quotes/import',files={'file':('quotes.csv',quote,'text/csv')})
    assert x.status_code==200,x.text
    x=client.post('/api/options/run',json={'source':'signal_set','symbol':'SPY','signal_set_id':signal_id})
    assert x.status_code==200,x.text
    assert x.json()['status']=='ok'
    assert x.json()['coverage']['filled']==1
    assert x.json()['run']['metrics']['net_pnl']==21.7


def test_v2_no_quote_no_fake_profit(tmp_path,monkeypatch):
    monkeypatch.setattr(server,'DB_PATH',tmp_path/'missing.sqlite3')
    client=TestClient(server.app)
    sig=b'entry_at,exit_at,side,underlying_entry_price\n2026-10-01T10:00:00-04:00,2026-10-01T10:15:00-04:00,LONG,700.5\n'
    sid=client.post('/api/signals/import',data={'symbol':'SPY','algorithm':'DELPHI_3'},files={'file':('signals.csv',sig,'text/csv')}).json()['id']
    r=client.post('/api/options/run',json={'source':'signal_set','symbol':'SPY','signal_set_id':sid})
    assert r.status_code==200,r.text
    assert r.json()['status']=='no_trades'
    assert client.get('/api/backtests').json()==[]


def test_premium_bracket_uses_observed_bid_and_timestamp(tmp_path):
    db=server.sqlite3.connect(tmp_path/'premium.sqlite3');db.row_factory=server.sqlite3.Row
    contract='O:SPY261001C00700500'
    quotes=(f'timestamp,contract,bid,ask\n2026-10-01T10:00:01-04:00,{contract},1.25,1.30\n'
            f'2026-10-01T10:04:00-04:00,{contract},1.75,1.80\n'
            f'2026-10-01T10:15:01-04:00,{contract},1.42,1.48\n').encode()
    store_quotes(db,csv_quotes(quotes))
    signal={'entry_at':'2026-10-01T10:00:00-04:00','exit_at':'2026-10-01T10:15:00-04:00',
            'side':'LONG','underlying_entry_price':700.5}
    trades,info=replay_0dte(db,[signal],'SPY',exit_mode='premium_bracket',premium_stop_pct=.25,premium_reward_r=1)
    assert info['filled']==1
    assert trades[0]['exit_reason']=='option premium target'
    assert trades[0]['exit_price']==1.74
    assert trades[0]['exit_quote_delay_s'] is None


def test_massive_adapter_parses_timestamp_and_preserves_true_bars(monkeypatch):
    def fake_get_json(path,params):
        assert path.startswith('/v2/aggs/ticker/SPY/range/3/minute/')
        return [{'t':1790850600000,'o':700,'h':701,'l':699,'c':700.5,'v':12345},
                {'t':1790850780000,'o':700.5,'h':701.5,'l':700,'c':701,'v':9999}]
    monkeypatch.setattr(market_v2,'get_json',fake_get_json)
    df,meta=market_v2.massive_stock_history('SPY','3m','2026-10-01','2026-10-01')
    assert len(df)==2 and df.iloc[0]['Close']==700.5 and 'Massive' in meta['source']


def test_reject_daily_bar_v5_port(tmp_path,monkeypatch):
    monkeypatch.setattr(server,'DB_PATH',tmp_path/'reject.sqlite3')
    r=TestClient(server.app).post('/api/strategy/run',json={'algorithm':'KAIROS_V5_CORE_PORT','symbol':'SPY','interval':'1d'})
    assert r.status_code==422 and 'intraday' in r.json()['detail']


def test_signal_import_with_exact_0dte_contract():
    raw=b'entry_at,exit_at,side,underlying_entry_price,contract\n2026-10-01T10:00:00-04:00,2026-10-01T10:15:00-04:00,LONG,700.5,O:SPY261001C00700500\n'
    signals=signals_from_csv(raw)
    assert signals[0]['contract']=='O:SPY261001C00700500'
    with pytest.raises(ValueError):signals_from_csv(raw.replace(b'261001',b'261002'))


def test_massive_auto_without_dates_caches_without_undefined_dates(tmp_path,monkeypatch):
    monkeypatch.setattr(server,'DB_PATH',tmp_path/'massive.sqlite3')
    monkeypatch.setenv('AION_MASSIVE_API_KEY','FAKE_TEST_KEY')
    # Dates are genuinely generated inside the download function.
    def fake_massive(symbol,interval,start,end):
        ix=pd.date_range(pd.Timestamp.now(tz='America/New_York').date().isoformat()+' 09:30',periods=2,freq='3min',tz='America/New_York')
        df=pd.DataFrame({'Open':[700,700.2],'High':[701,701], 'Low':[699,700],
                         'Close':[700.2,700.5], 'Volume':[100,120]},index=ix)
        return df,{'source':'Massive mocked provider','last_bar':df.index[-1].isoformat()}
    monkeypatch.setattr(server,'massive_stock_history',fake_massive)
    frame,meta=server.load_data_for_backtest('SPY','3m','5d','auto')
    assert len(frame)==2 and meta['source']=='Massive mocked provider'
