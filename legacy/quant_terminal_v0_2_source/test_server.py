import io
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import server


def test_trade_csv_and_metrics():
    raw=b'date,side,pnl\n2026-10-01,LONG,100\n2026-10-02,SHORT,-40\n2026-10-03,LONG,50\n'
    trades=server.csv_to_trades(raw)
    metrics=server.trade_metrics(trades)
    assert len(trades)==3
    assert metrics['net_pnl']==110
    assert metrics['win_rate']==66.7
    assert metrics['max_drawdown_usd']==40
    assert metrics['profit_factor']==3.75


def test_tv_export_entry_rows_not_counted():
    raw=b'Trade #,Type,Date and time,Net P&L USD\n1,Entry long,2026-10-01 09:30,0\n1,Exit long,2026-10-01 09:35,85.55\n2,Entry short,2026-10-01 10:00,0\n2,Exit short,2026-10-01 10:20,-24.12\n'
    trades=server.csv_to_trades(raw)
    assert [x['pnl'] for x in trades]==[85.55,-24.12]


def test_bad_csv_returns_error():
    with pytest.raises(Exception):server.csv_to_trades(b'price,symbol\n100,SPY\n')


def test_too_short_regime():
    df=pd.DataFrame({'Close':np.linspace(100,101,80)})
    out=server.compute_regime(df)
    assert out['status']=='insufficient'


def sample_bars():
    rng=np.random.default_rng(17)
    index=pd.date_range('2026-10-01 09:30',periods=280,freq='5min',tz='America/New_York')
    c=755+np.cumsum(rng.normal(0,.4,len(index)))
    o=np.r_[c[0],c[:-1]]
    return pd.DataFrame({'Open':o,'Close':c,'High':np.maximum(o,c)+.25,
                         'Low':np.minimum(o,c)-.25,'Volume':50000},index=index)


def test_ohlc_serialization():
    bars=server.bars_json(sample_bars(),max_points=10)
    assert len(bars)==10
    assert all(set(item)=={'t','o','h','l','c','v'} for item in bars)


def test_research_brackets_do_not_invent_pnl():
    trades=server.research_backtest(sample_bars(),'NW_FLIP_RESEARCH')
    for trade in trades:
        assert math.isfinite(trade['pnl'])
        assert trade['qty']>=1


def test_website_and_mocked_overview(monkeypatch,tmp_path):
    monkeypatch.setattr(server,'DB_PATH',tmp_path/'test.sqlite3')
    monkeypatch.setattr(server,'fetch_bars',lambda symbol,interval,period:
        (sample_bars(),{'source':'Mock data test','realtime':False,'last_bar':'2026-10-03T15:55:00-04:00'}))
    monkeypatch.setattr(server,'news_and_sentiment',lambda symbol:([],{'tone':'Unavailable','score':None,'count':0}))
    monkeypatch.setattr(server,'compute_regime',lambda df:{'status':'insufficient'})
    client=TestClient(server.app)
    assert client.get('/').status_code==200
    assert client.get('/api/overview?symbol=SPY&interval=5m&period=1mo').json()['symbol']=='SPY'
    assert client.get('/api/backtests').json()==[]
    response=client.post('/api/backtests/import',data={'algorithm':'KAIROS_V5','symbol':'SPY','timeframe':'3m'},
                         files={'file':('trades.csv',b'date,side,pnl\n2026-10-01,LONG,100\n2026-10-02,SHORT,-25\n','text/csv')})
    assert response.status_code==200, response.text
    assert response.json()['metrics']['net_pnl']==75
    assert len(client.get('/api/backtests').json())==1
    assert client.get('/api/backtests/1').json()['metrics']['trades']==2


def test_tv_sync_requires_token_and_preserves_source(monkeypatch,tmp_path):
    monkeypatch.setattr(server,'DB_PATH',tmp_path/'tv.sqlite3')
    monkeypatch.setenv('AION_TV_SYNC_TOKEN','test-secret-not-production')
    client=TestClient(server.app)
    payload={'symbol':'SPY','interval':'5m','ohlcv':{'bars':{
        't':[1793716200,1793716500],
        'o':[754.40,755.05],'h':[756.00,756.07],'l':[753.25,754.65],
        'c':[755.08,755.90],'v':[140000,122000]}},
        'news':{'data':{'headlines':[{'title':'Market earnings support rally','published':1793716500,'provider':{'name':'Example News'}}]}}}
    assert client.post('/api/tv-sync',json=payload).status_code==401
    r=client.post('/api/tv-sync',json=payload,headers={'X-Aion-Sync-Token':'test-secret-not-production'})
    assert r.status_code==200,r.text
    snap=server.tv_snapshot('SPY','5m')
    assert snap is not None
    assert len(snap[0]['bars'])==2
    assert list(server.frame_from_tv(snap[0]['bars']).columns)==['Open','High','Low','Close','Volume']
