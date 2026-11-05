from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi.testclient import TestClient
import aion_execution_journal as ledger
import aion_live_dashboard as dashboard

OCC = 'SPY   261105C00767000'


def test_live_monitor_is_read_only():
    routes = {(r.path, tuple(r.methods or [])) for r in dashboard.app.routes}
    assert not any('POST' in methods or 'PUT' in methods or 'DELETE' in methods for _, methods in routes)
    c = TestClient(dashboard.app)
    assert c.get('/health').json()['execution_enabled'] is False
    assert c.post('/api/snapshot').status_code == 405


def test_record_fill_is_idempotent_and_pnl_is_fifo():
    with TemporaryDirectory() as td:
        path=Path(td)/'test.sqlite3'
        a=dict(fill_id='fill-1', filled_at='2026-11-05T15:03:00Z', option_symbol=OCC,
               mode='PAPER', action='BUY_TO_OPEN', contracts=1, fill_price=0.89,
               fees_usd=0.5, source='PAPER-BROKER')
        assert ledger.record_fill(db_path=path,**a) is True
        assert ledger.record_fill(db_path=path,**a) is False
        ledger.record_fill(db_path=path,fill_id='fill-2',filled_at='2026-11-05T15:12:00Z',option_symbol=OCC,
                mode='PAPER',action='SELL_TO_CLOSE',contracts=1,fill_price=1.10,fees_usd=0.5,source='PAPER-BROKER')
        data=ledger.read_fills(path)
        s=ledger.summarize_fills(data)
        assert len(data)==2
        assert s['realized_pnl_usd']['PAPER']==20.0
        assert s['open_contracts']['PAPER']==0


def test_aion_offline_still_shows_page(monkeypatch):
    monkeypatch.setattr(dashboard,'fetch_endpoint',lambda p: (_ for _ in ()).throw(TimeoutError()))
    c=TestClient(dashboard.app)
    r=c.get('/api/snapshot')
    assert r.status_code==200
    assert r.json()['aion_online'] is False
    assert r.json()['events']==[]


def test_non_spy_fill_rejected():
    with TemporaryDirectory() as td:
        try:
            ledger.record_fill(db_path=Path(td)/'db',fill_id='bad',filled_at='2026-11-05T15:03:00Z',
            option_symbol='QQQ   261105C00600000',mode='PAPER',action='BUY_TO_OPEN',contracts=1,fill_price=0.89,source='PAPER')
        except ValueError:
            pass
        else:
            raise AssertionError('Non-SPY contract must be rejected')
