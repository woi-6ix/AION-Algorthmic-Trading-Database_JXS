"""Offline AION Alpaca PAPER regression tests. NO NETWORK and NO real orders."""
from __future__ import annotations
import sys, sqlite3, importlib.util
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
import pytest

PARENT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(PARENT))
import aion_alpaca_paper_algo as mod
import aion_execution_journal as journal

FIXED=datetime(2026,11,5,18,0,tzinfo=timezone.utc) # 1 PM ET
CALL='SPY261105C00736000'
PUT='SPY261105P00735000'

class FakePaperAPI:
    def __init__(self):
        self.all={}
        self.submissions=[]
    def account(self):return {'status':'ACTIVE','id':'fake-paper-account','options_trading_level':3,'cash':'100000','trading_blocked':False,'account_blocked':False}
    def clock(self):return {'is_open':True}
    def positions(self):
        lots={}
        for o in self.all.values():
            q=int(Decimal(o['filled_qty']))
            lots[o['symbol']]=lots.get(o['symbol'],0)+(q if o['side']=='buy' else -q)
        return [{'symbol':s,'asset_class':'us_option','qty':str(q),'side':'long'} for s,q in lots.items() if q>0]
    def orders(self):
        return [x for x in self.all.values() if x['status'] not in mod.TERMINAL]
    def stock_spy(self):return Decimal('735.415')
    def contracts(self,expiry,right):
        symbol=CALL if right=='call' else PUT
        return [{'symbol':symbol,'underlying_symbol':'SPY','expiration_date':expiry,'type':right,'tradable':True,'multiplier':'100','strike_price':'736' if right=='call' else '735'}]
    def snapshot(self,symbols):
        return {s:{'latestQuote':{'bp':'2.54','ap':'2.56','t':FIXED.isoformat()}} for s in symbols}
    def submit(self,body):
        assert body['position_intent'] in {'buy_to_open','sell_to_close'}
        assert body['type']=='limit' and body['time_in_force']=='day'
        assert int(body['qty'])>=1 and body['symbol'] in (CALL,PUT)
        assert body['client_order_id'] not in self.all
        o=dict(body)
        o.update({'status':'new','filled_qty':'0','filled_avg_price':None,'updated_at':FIXED.isoformat(),'filled_at':None,'id':'paper-order-'+str(len(self.all)+1)})
        self.all[body['client_order_id']]=o
        self.submissions.append(o)
        return o
    def order_by_client_id(self,cid):return self.all.get(cid)
    def cancel(self,oid):
        for item in self.all.values():
            if item['id']==oid:
                item['status']='canceled'
                return {}
        raise RuntimeError('Order ID missing')
    def fill(self,cid,qty,avg='2.57'):
        o=self.all[cid]
        o['filled_qty']=str(qty)
        o['filled_avg_price']=str(avg)
        o['updated_at']=FIXED.isoformat()
        o['status']='filled' if qty==int(o['qty']) else 'partially_filled'
        o['filled_at']=FIXED.isoformat() if o['status']=='filled' else None

@pytest.fixture(autouse=True)
def isolated_state(tmp_path,monkeypatch):
    monkeypatch.setattr(mod,'ROOT',tmp_path)
    monkeypatch.setattr(mod,'STATE',tmp_path/'aion_alpaca_paper_algo.sqlite3')
    monkeypatch.setattr(mod,'SIGNALS',tmp_path/'aion_kairos_v5_signals.sqlite3')
    monkeypatch.setattr(mod,'KILL',tmp_path/'AION_PAPER_KILL_SWITCH.txt')
    monkeypatch.setattr(mod,'LOCK',tmp_path/'.worker.lock')
    monkeypatch.setattr(mod,'now_utc',lambda:FIXED)
    monkeypatch.setattr(journal,'DEFAULT_DB',tmp_path/'aion_execution_journal.sqlite3')
    # record_fill binds default_db at function definition, so bind a safe wrapper
    original=journal.record_fill
    def local_fill(**kwargs):return original(**kwargs, db_path=tmp_path/'aion_execution_journal.sqlite3')
    monkeypatch.setattr(journal,'record_fill',local_fill)
    mod.init_db()
    mod.put_cfg('paper_account_id','fake-paper-account')
    mod.put_cfg('armed','true')
    mod.put_cfg('armed_at',(FIXED-timedelta(seconds=30)).isoformat())
    yield


def plan(api,signal='BUY_CALL',event='test-signal'):
    return mod.entry_order(api,{'signal':signal,'event_id':event})

def close(api,event='test-close'):
    return mod.exit_order(api,event,'KAIROS_V5_EXIT')

def test_2500_premium_sizing():
    assert mod.contract_quantity('2.57','100000')==9
    assert 9*Decimal('2.57')*100<=mod.MAX_COST
    assert mod.contract_quantity('2.57','500')==1
    assert mod.contract_quantity('30','100000')==0
    assert mod.contract_quantity('0.01','100000')==mod.MAX_CONTRACTS


def test_limit_order_uses_paper_budget_multiple_contracts():
    api=FakePaperAPI()
    result=plan(api)
    assert '9x' in result
    order=api.submissions[0]
    assert order['side']=='buy'
    assert int(order['qty'])==9
    assert Decimal(order['limit_price'])*int(order['qty'])*100<=Decimal('2500')
    assert len(mod.orders_db())==1


def test_entry_cap_checks_total_quantity_not_one_contract():
    api=FakePaperAPI()
    with pytest.raises(RuntimeError,match='2,500'):
        mod.submit_intent(api,'over-budget','BUY_TO_OPEN',CALL,'2.60',qty=10)
    assert not api.submissions


def test_disarmed_rejects_order_even_with_live_quote():
    api=FakePaperAPI()
    mod.put_cfg('armed','false')
    with pytest.raises(RuntimeError,match='Not armed'):
        plan(api)
    assert not api.submissions


def test_full_size_paper_exit_is_kairos_signal_driven():
    api=FakePaperAPI()
    plan(api)
    entry=api.submissions[0]
    api.fill(entry['client_order_id'],9,'2.57')
    mod.sync_orders(api)
    assert mod.last_owned_symbol()==CALL
    msg=close(api)
    assert '9x' in msg
    sale=api.submissions[1]
    assert sale['side']=='sell' and int(sale['qty'])==9
    api.fill(sale['client_order_id'],9,'2.90')
    mod.sync_orders(api)
    fills=journal.read_fills(db_path=journal.DEFAULT_DB)
    assert len(fills)==2
    assert sorted((r['contracts'] for r in fills))==[9,9]
    assert journal.summarize_fills(fills)['realized_pnl_usd']['PAPER']==pytest.approx(297)
    assert mod.last_owned_symbol() is None


def test_partial_fills_are_incrementally_journaled_and_idempotent():
    api=FakePaperAPI();plan(api)
    cid=api.submissions[0]['client_order_id']
    api.fill(cid,3,'2.50');mod.sync_orders(api)
    api.fill(cid,9,'2.55');mod.sync_orders(api)
    mod.sync_orders(api)
    fills=journal.read_fills(db_path=journal.DEFAULT_DB)
    assert len(fills)==2
    assert sorted(r['contracts'] for r in fills)==[3,6]
    total=sum(Decimal(str(f['fill_price']))*f['contracts']*100 for f in fills)
    assert abs(total-Decimal('2295'))<Decimal('.01')


def test_exit_order_requires_matching_broker_position_and_local_fills():
    api=FakePaperAPI();plan(api)
    o=api.submissions[0];api.fill(o['client_order_id'],9);mod.sync_orders(api)
    # An unauthorized manual change at broker must prevent automated closes.
    def mismatch():return [{'symbol':CALL,'asset_class':'us_option','qty':'8','side':'long'}]
    api.positions=mismatch
    with pytest.raises(RuntimeError,match='differs'):
        close(api)
    assert len(api.submissions)==1


def test_pending_entry_is_not_silently_closed_and_worker_halts():
    api=FakePaperAPI();plan(api)
    with pytest.raises(RuntimeError,match='open SPY option order'):
        close(api)
    assert len(api.submissions)==1


def test_reversal_closes_call_before_buying_put():
    api=FakePaperAPI();plan(api)
    o=api.submissions[0];api.fill(o['client_order_id'],9);mod.sync_orders(api)
    # An opposing Pine strategy.entry is a reversal signal even without a separate EXIT.
    with sqlite3.connect(mod.SIGNALS) as db:
        db.execute('''CREATE TABLE events (event_id TEXT,received_at TEXT,signal TEXT,result TEXT,strategy TEXT,symbol TEXT)''')
        db.execute('INSERT INTO events VALUES(?,?,?,?,?,?)',('flip123',FIXED.isoformat(),'BUY_PUT','REVERSAL_INTENT_LOGGED','KAIROS_V5','SPY'))
    result=mod.process_events(api)
    assert 'EXIT_ORDER' in result[0][1]
    assert len(api.submissions)==2 and api.submissions[-1]['side']=='sell'
    # NO put entry before full call close.
    assert mod.pending_reversal() is not None
    assert mod.finish_pending_reversal(api)=='REVERSAL_WAITING_FOR_EXIT_FILL'
    sell=api.submissions[-1];api.fill(sell['client_order_id'],9,'2.45');mod.sync_orders(api)
    res=mod.finish_pending_reversal(api)
    assert 'REVERSAL_ENTRY' in res
    assert len(api.submissions)==3
    assert api.submissions[-1]['symbol']==PUT and api.submissions[-1]['side']=='buy'
    assert mod.pending_reversal() is None


def test_premium_tp_sl_no_longer_automatically_close():
    api=FakePaperAPI();plan(api)
    entry=api.submissions[0];api.fill(entry['client_order_id'],9,'2.57');mod.sync_orders(api)
    # Quote bid is lower than a hypothetical old premium stop threshold.
    assert mod.risk_exit(api) is None
    assert len(api.submissions)==1


def test_data_host_is_fixed_paper_only_and_chart_end_omitted():
    assert mod.TRADING=='https://paper-api.alpaca.markets'
    assert mod.DATA=='https://data.alpaca.markets'
    def fake_call(method,route,*,data=False,params=None,body=None):
        assert method=='GET' and data is True
        assert route=='/v1beta1/options/bars'
        assert 'end' not in params
        return {'bars':{CALL:[]}}
    api=object.__new__(mod.AlpacaPaper)
    api.call=fake_call
    assert api.bars(CALL,FIXED.isoformat(),None)==[]


def test_legacy_one_contract_database_migration(tmp_path,monkeypatch):
    path=tmp_path/'old_orders.sqlite3'
    with sqlite3.connect(path) as db:
        db.execute('''CREATE TABLE paper_orders (client_order_id TEXT PRIMARY KEY,event_id TEXT NOT NULL,submitted_at TEXT NOT NULL,action TEXT NOT NULL,option_symbol TEXT NOT NULL,qty INTEGER NOT NULL,state TEXT NOT NULL,broker_order_id TEXT,filled_qty TEXT NOT NULL DEFAULT '0',fill_price TEXT,filled_at TEXT,journaled INTEGER NOT NULL DEFAULT 0,last_update TEXT)''')
        db.execute('INSERT INTO paper_orders VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
                   ('old','old_event',FIXED.isoformat(),'BUY_TO_OPEN',CALL,1,'filled','old-broker','1','2.55',FIXED.isoformat(),1,FIXED.isoformat()))
    monkeypatch.setattr(mod,'STATE',path)
    mod.init_db()
    row=mod.orders_db()[0]
    assert row['journaled_qty']==1
    assert Decimal(row['journaled_notional'])==Decimal('2.55')


def test_server_accepts_opposing_kairos_signal_as_reversal(monkeypatch,tmp_path):
    # Importing source is safe, but it builds a local temporary simulator DB.
    monkeypatch.setenv('SNAPTRADE_CASH_ACCOUNT_ID','REDACTED_TEST_ONLY')
    monkeypatch.setenv('AION_MODE','SIMULATION')
    spec=importlib.util.spec_from_file_location('aion_kairos_server_test',PARENT/'aion_kairos_server.py')
    svr=importlib.util.module_from_spec(spec)
    sys.modules[spec.name]=svr
    spec.loader.exec_module(svr)
    monkeypatch.setattr(svr,'DB',tmp_path/'signals.sqlite3')
    svr.init_db()
    from fastapi.testclient import TestClient
    cli=TestClient(svr.app)
    d={'strategy':'KAIROS_V5','symbol':'SPY','timeframe':'3m','emitted_at':datetime.now(timezone.utc).isoformat()}
    first=cli.post('/webhook',json={'event_id':'entry101','signal':'BUY_CALL',**d})
    second=cli.post('/webhook',json={'event_id':'reversal202','signal':'BUY_PUT',**d})
    assert first.json()['result']=='SIGNAL_LOGGED'
    assert second.json()['result']=='REVERSAL_INTENT_LOGGED'
    assert second.json()['intent_state']=='PUT_INTENT'
    assert second.json()['broker_order_sent'] is False


def test_kairos_exit_cancels_unfilled_entry_before_closing():
    api=FakePaperAPI();plan(api)
    cid=api.submissions[0]['client_order_id']
    result=mod.request_strategy_exit(api,'exit-after-open','KAIROS_V5_EXIT')
    assert result=='EXIT_WAITING_BROKER_ENTRY_CANCEL'
    assert api.all[cid]['status']=='canceled'
    mod.sync_orders(api)
    assert mod.process_pending_exit(api)=='NO_BROKER_POSITION'
    assert mod.pending_exit() is None
    assert len(api.submissions)==1


def test_partial_entry_then_exit_sells_only_confirmed_contracts():
    api=FakePaperAPI();plan(api)
    first=api.submissions[0]
    api.fill(first['client_order_id'],3,'2.60')
    mod.sync_orders(api)
    assert mod.request_strategy_exit(api,'partial-exit','KAIROS_V5_EXIT')=='EXIT_WAITING_BROKER_ENTRY_CANCEL'
    mod.sync_orders(api)
    outcome=mod.process_pending_exit(api)
    assert '3x' in outcome
    assert len(api.submissions)==2
    assert api.submissions[-1]['qty']=='3'


def test_reversal_cancels_unfilled_opposing_entry_and_then_opens_put():
    api=FakePaperAPI();plan(api)
    old=api.submissions[0]
    with sqlite3.connect(mod.SIGNALS) as db:
        db.execute('CREATE TABLE events (event_id TEXT,received_at TEXT,signal TEXT,result TEXT,strategy TEXT,symbol TEXT)')
        db.execute('INSERT INTO events VALUES(?,?,?,?,?,?)',('flip-unfilled',FIXED.isoformat(),'BUY_PUT','REVERSAL_INTENT_LOGGED','KAIROS_V5','SPY'))
    res=mod.process_events(api)
    assert 'EXIT_WAITING_BROKER_ENTRY_CANCEL' in res[0][1]
    assert api.all[old['client_order_id']]['status']=='canceled'
    mod.sync_orders(api)
    assert mod.process_pending_exit(api)=='NO_BROKER_POSITION'
    assert 'REVERSAL_ENTRY' in mod.finish_pending_reversal(api)
    assert api.submissions[-1]['symbol']==PUT
    assert len(api.submissions)==2


def test_distinct_new_same_direction_pine_fill_is_logged(monkeypatch,tmp_path):
    monkeypatch.setenv('AION_MODE','SIMULATION')
    spec=importlib.util.spec_from_file_location('aion_kairos_server_same_side_test',PARENT/'aion_kairos_server.py')
    svr=importlib.util.module_from_spec(spec)
    sys.modules[spec.name]=svr
    spec.loader.exec_module(svr)
    monkeypatch.setattr(svr,'DB',tmp_path/'signals.sqlite3')
    svr.init_db()
    from fastapi.testclient import TestClient
    c=TestClient(svr.app)
    body={'strategy':'KAIROS_V5','symbol':'SPY','timeframe':'3m','emitted_at':datetime.now(timezone.utc).isoformat(),'signal':'BUY_CALL'}
    a=c.post('/webhook',json={**body,'event_id':'same-side-event-1'}).json()
    b=c.post('/webhook',json={**body,'event_id':'same-side-event-2'}).json()
    replay=c.post('/webhook',json={**body,'event_id':'same-side-event-2'}).json()
    assert a['result']=='SIGNAL_LOGGED'
    assert b['result']=='SIGNAL_LOGGED'
    assert replay['result']=='DUPLICATE'