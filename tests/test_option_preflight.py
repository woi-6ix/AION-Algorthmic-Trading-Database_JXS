"""Offline/read-only preflight tests; no credentials or HTTP requests."""
from __future__ import annotations
import io
import json
import pathlib
import sys
from datetime import datetime, timezone
from urllib.error import HTTPError

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import aion_option_preflight as x


def test_session_is_capped_at_least_twenty_minutes_behind():
    start, end = x.option_session('SPY261105C00736000', datetime(2026, 11, 5, 18, 40, tzinfo=timezone.utc))
    assert start.startswith('2026-11-05T14:30:00')
    assert end.startswith('2026-11-05T18:20:00')


def test_expired_session_end_is_4pm_et():
    start, end = x.option_session('SPY261105C00736000', datetime(2026, 11, 6, 20, tzinfo=timezone.utc))
    assert end.startswith('2026-11-05T21:00:00')


def test_invalid_contract_rejected():
    try:
        x.option_session('SPY')
    except ValueError:
        pass
    else:
        raise AssertionError('Contract symbol validation missing')


def test_response_never_prints_api_keys():
    msg = b'{"message": "Invalid token=AKABCDEF12345678901234 URL https://example.invalid/sensitive"}'
    clean = x.clean_error_message(msg)
    assert 'AKABCDEF12345678901234' not in clean
    assert 'example.invalid' not in clean


def test_only_get_requests_to_market_data(monkeypatch):
    calls = []
    class Resp:
        status=200
        def __enter__(self):return self
        def __exit__(self,*args):return False
        def read(self):return json.dumps({'bars':{'SPY261105C00736000':[{'t':'2026-11-05T18:00:00Z'}]}}).encode()
    class Api:
        headers={'Accept':'application/json'}
    def op(req, timeout):
        calls.append(req)
        return Resp()
    monkeypatch.setattr(x.urllib.request, 'urlopen', op)
    good=x.run_readonly_chart_probe(Api(),'SPY261105C00736000',datetime(2026,11,5,20,tzinfo=timezone.utc))
    assert good
    assert len(calls)==1
    assert calls[0].get_method() == 'GET'
    assert calls[0].full_url.startswith('https://data.alpaca.markets/v1beta1/options/bars?')


def test_fallback_on_400_and_no_post(monkeypatch):
    called=[]
    class Resp:
        def __enter__(self):return self
        def __exit__(self,*args):return False
        def read(self):return json.dumps({'bars':{'SPY261105C00736000':[{'t':'2026-11-05T18:00:00Z'}]}}).encode()
    class Api:headers={}
    def op(req, timeout):
        called.append(req)
        if len(called)==1:
            raise HTTPError(req.full_url,400,'400',{},io.BytesIO(b'{"message":"invalid request"}'))
        return Resp()
    monkeypatch.setattr(x.urllib.request,'urlopen',op)
    assert x.run_readonly_chart_probe(Api(),'SPY261105C00736000',datetime(2026,11,5,20,tzinfo=timezone.utc))
    assert len(called)==2
    assert all(r.get_method()=='GET' and 'paper-api' not in r.full_url for r in called)