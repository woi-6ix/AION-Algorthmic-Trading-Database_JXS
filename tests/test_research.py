"""Offline regression tests; no market network required."""
import json
import sys
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import aion_research as m

app = FastAPI()
app.include_router(m.router)
client=TestClient(app)

def fake_bars():
    ix=pd.date_range('2026-01-01',periods=200, freq='B')
    close=pd.Series(100+np.arange(200)*.08+np.sin(np.arange(200)/8), index=ix)
    return pd.DataFrame({'Close':close,'Volume':1_000_000+np.arange(200)*100},index=ix)

def test_valid_symbols_and_limit():
    assert m.parse_symbols('spy,QQQ,spy')==['SPY','QQQ']
    assert client.get('/api/research/screener',params={'tickers':'SPY,../malicious'}).status_code==400
    assert client.get('/api/research/screener',params={'tickers':','.join('A'+str(i) for i in range(13))}).status_code==400

def test_screener_and_chart_offline():
    with patch.object(m,'_history',return_value=fake_bars()):
        res=client.get('/api/research/screener',params={'tickers':'SPY,QQQ'});assert res.status_code==200
        body=res.json();assert len(body['rows'])==2
        assert body['rows'][0]['rsi_14'] is not None
        assert 'not entire US market' in body['universe']
        chart=client.get('/api/research/chart',params={'symbol':'SPY'});assert chart.status_code==200
        assert chart.json()['series'][-1]['date']

def test_screener_fail_closed():
    with patch.object(m,'_history',side_effect=RuntimeError('NO DATA')):
        res=client.get('/api/research/screener',params={'tickers':'SPY'}).json()
        assert res['rows']==[] and res['errors'][0]['symbol']=='SPY'
        assert client.get('/api/research/chart',params={'symbol':'SPY'}).status_code==503

def test_news_scoring_and_schema():
    with patch.object(m,'_get_news',return_value=[{'title':'title','url':'https://news.google.com/a','source':'foo','published':'','sentiment':.4,'label':'Positive'}]):
        m.CACHE.clear()
        res=client.get('/api/research/sentiment',params={'symbol':'SPY'});assert res.status_code==200
        assert res.json()['average_compound']==.4
        assert 'not the original HG' in res.json()['method']

def test_regime_proxy_explicit():
    with patch.object(m,'_history',return_value=fake_bars()):
        r=client.get('/api/research/regime',params={'symbol':'SPY'})
        assert r.status_code==200
        assert 'NOT the original' in r.json()['storm']['warning']
        assert r.json()['timeframe'].startswith('daily')

def test_terminal_is_restricted_local_probe():
    with patch.object(m,'urlopen',side_effect=ConnectionRefusedError('offline')):
        r=client.get('/api/research/terminal')
        assert r.status_code==200 and r.json()['online'] is False

def test_security_no_trading_routes():
    routes=[r.path for r in m.router.routes]
    assert all('/trade' not in s and '/order' not in s for s in routes)
    assert all('GET' in r.methods and 'POST' not in r.methods for r in m.router.routes)
