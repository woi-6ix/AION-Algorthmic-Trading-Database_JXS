"""Regression tests for Alpaca historical options bars: OFFLINE, no real credentials/orders."""
from __future__ import annotations

import io
import json
import pathlib
import sqlite3
import sys
from datetime import datetime, timezone
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
import aion_alpaca_paper_algo as algo
import aion_alpaca_paper_panel as panel
import aion_option_preflight as preflight
import install_aion_bars_feed_fix as installer

SYMBOL='SPY261105C00736000'


def fake_api():
    obj=object.__new__(algo.AlpacaPaper)  # No network/credentials
    calls=[]
    def call(method, path, *, data=False, params=None, body=None):
        calls.append((method,path,data,params,body))
        return {'bars':{SYMBOL:[{'t':'2026-11-05T18:30:00Z','c':2.56,'o':2.51,'h':2.58,'l':2.50,'v':5}]}}
    obj.call=call
    return obj,calls


def test_bars_no_feed_capped_before_live_data(monkeypatch):
    monkeypatch.setattr(algo,'now_utc',lambda: datetime(2026,11,5,19,0,tzinfo=timezone.utc))
    api,calls=fake_api()
    bars=api.bars(SYMBOL,'2026-11-05T14:30:00+00:00')
    assert bars and len(calls)==1
    meth,route,data,params,body=calls[0]
    assert (meth,route,data,body)==('GET','/v1beta1/options/bars',True,None)
    assert 'feed' not in params
    assert params['timeframe']=='3Min'
    assert params['end']=='2026-11-05T18:40:00+00:00'


def test_bars_respect_earlier_explicit_end(monkeypatch):
    monkeypatch.setattr(algo,'now_utc',lambda: datetime(2026,11,5,19,0,tzinfo=timezone.utc))
    api,calls=fake_api()
    api.bars(SYMBOL,'2026-11-05T14:30:00+00:00','2026-11-05T17:10:00+00:00')
    assert calls[0][3]['end']=='2026-11-05T17:10:00+00:00'


def test_bars_restrict_to_expiry_close(monkeypatch):
    monkeypatch.setattr(algo,'now_utc',lambda: datetime(2026,11,6,21,0,tzinfo=timezone.utc))
    api,calls=fake_api()
    api.bars(SYMBOL,'2026-11-05T14:30:00+00:00')
    assert calls[0][3]['end']=='2026-11-05T21:00:00+00:00'


def test_bars_not_yet_available_returns_empty_without_call(monkeypatch):
    monkeypatch.setattr(algo,'now_utc',lambda: datetime(2026,11,5,14,40,tzinfo=timezone.utc))
    api,calls=fake_api()
    assert api.bars(SYMBOL,'2026-11-05T14:30:00+00:00')==[]
    assert not calls


def test_bars_reject_non_spy_contract():
    api,calls=fake_api()
    with pytest.raises(RuntimeError, match='SPY OCC contract'):
        api.bars('AAPL261105C00736000','2026-11-05T14:30:00+00:00')
    assert not calls


def test_preflight_historical_bar_request_never_has_feed(monkeypatch):
    calls=[]
    class Result:
        def __enter__(self):return self
        def __exit__(self,*args):return False
        def read(self):return json.dumps({'bars':{SYMBOL:[{'t':'2026-11-05T18:40:00Z'}]}}).encode()
    class Stub:headers={'Accept':'application/json'}
    def op(req, timeout):
        calls.append(req)
        assert req.get_method()=='GET'
        assert urlparse(req.full_url).netloc=='data.alpaca.markets'
        assert 'feed' not in parse_qs(urlparse(req.full_url).query)
        return Result()
    monkeypatch.setattr(preflight.urllib.request,'urlopen',op)
    assert preflight.run_readonly_chart_probe(Stub(),SYMBOL,datetime(2026,11,5,20,tzinfo=timezone.utc))
    assert len(calls)==1


def test_chart_response_is_historical_trade_bars(monkeypatch):
    monkeypatch.setattr(panel,'now_utc',lambda: datetime(2026,11,6,20,tzinfo=timezone.utc))
    class Fake:
        def bars(self,*args):return [{'t':'2026-11-05T20:55:00Z','o':1,'h':2,'l':1,'c':1.25,'v':10}]
    monkeypatch.setattr(panel,'AlpacaPaper',lambda:Fake())
    client=TestClient(panel.app)
    res=client.get('/api/bars',params={'symbol':SYMBOL})
    assert res.status_code==200
    assert res.json()['source']=='historical_option_trade_bars'
    assert res.json()['bars'][0]['c']==1.25
    assert 'feed' not in res.json()
    html=client.get('/').text
    assert 'Historical option trade bars' in html


def test_installer_surgical_and_idempotent(tmp_path,monkeypatch):
    old=pathlib.Path(__file__).resolve().parents[1]/'aion_bars_feed_payload'/'original'
    for n in installer.NAMES:(tmp_path/n).write_text((old/n).read_text())
    monkeypatch.setattr(installer,'ROOT',tmp_path)
    installer.install()
    for n in installer.NAMES:
        actual=(tmp_path/n).read_text()
        assert "'feed':" not in actual[actual.index('    def bars(self, symbol, start, end=None):'):actual.index('\n\ndef broker_account_check(')] if n=='aion_alpaca_paper_algo.py' else True
    installer.install()
    assert len(list((tmp_path/'aion-options-bar-backups').iterdir()))==1


def test_installer_refuses_armed_bot(tmp_path,monkeypatch):
    state=tmp_path/'aion_alpaca_paper_algo.sqlite3'
    with sqlite3.connect(state) as conn:
        conn.execute('CREATE TABLE cfg (name TEXT PRIMARY KEY, value TEXT NOT NULL)')
        conn.execute("INSERT INTO cfg VALUES ('armed','true')")
    monkeypatch.setattr(installer,'ROOT',tmp_path)
    with pytest.raises(RuntimeError,match='ARMED'):
        installer.install()