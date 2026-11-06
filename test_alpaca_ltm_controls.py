"""Offline tests. Never calls Alpaca; all broker calls mocked."""
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from fastapi import FastAPI
from fastapi.testclient import TestClient

import aion_alpaca_control as ctl


def test_control_lifecycle(tmp_path, monkeypatch):
    worker = tmp_path / 'aion_alpaca_paper_algo.py'
    worker.write_text('''import json,sys,datetime,pathlib,os
path=pathlib.Path(__file__).with_suffix('.json')
state=json.loads(path.read_text()) if path.exists() else {"armed":True,"kill_switch":False,"account_bound":True}
cmd=sys.argv[1]
if cmd == 'disarm':state.update(armed=False,kill_switch=True)
if cmd == 'arm':
  assert input() == 'ARM ALPACA PAPER'
  assert os.environ.get('AION_LTM_ARM_APPROVAL') == os.environ.get('AION_CONTROL_TOKEN')
  state.update(armed=True,kill_switch=False)
path.write_text(json.dumps(state))
print(f"Paper bot armed: {state['armed']} | Kill switch: {state['kill_switch']} | Account bound: {state['account_bound']}")
print("Last heartbeat: " + datetime.datetime.now(datetime.timezone.utc).isoformat())
''')
    monkeypatch.setattr(ctl, 'ROOT', tmp_path)
    monkeypatch.setenv('AION_CONTROL_TOKEN', 'a'*48)
    data={'positions':[{'symbol':'SPY261106C00650000','qty':'2','asset_class':'us_option'}], 'orders': []}
    calls=[]
    def fake_broker(method,path,*,query=''):
        calls.append((method,path,query))
        if method == 'GET' and path == '/v2/positions':return (200,list(data['positions']))
        if method == 'GET' and path == '/v2/orders':return (200,list(data['orders']))
        if method == 'DELETE' and path.startswith('/v2/positions/'):
            assert not ctl.worker_status()['armed']
            data['positions'] = []
            return (200, {'id':'fake-order','status':'accepted','symbol':'SPY261106C00650000'})
        if method == 'DELETE' and path == '/v2/positions':
            assert not ctl.worker_status()['armed']
            n=len(data['positions'])
            data['positions']=[]
            return (207,[{'symbol':'SPY261106C00650000','status':200,'body':{'id':'order-123'}}] if n else [])
        raise RuntimeError((method,path,query))
    monkeypatch.setattr(ctl, 'broker', fake_broker)
    app = FastAPI();app.include_router(ctl.ROUTER)
    client = TestClient(app)
    url='/api/alpaca-control'
    assert client.get(url+'/state').status_code == 401
    h={'X-Aion-Control-Token':'a'*48, 'Origin':'http://testserver'}
    result=client.get(url+'/state',headers=h)
    assert result.status_code == 200 and result.json()['auto_trading_active']
    cross=client.post(url+'/pause',headers={**h, 'Origin':'https://foreign.example'},json={'confirm':'PAUSE PAPER BOT'})
    assert cross.status_code == 403
    paused=client.post(url+'/pause',headers=h,json={'confirm':'PAUSE PAPER BOT'})
    assert paused.status_code == 200 and not paused.json()['worker']['armed']
    resumed=client.post(url+'/resume',headers=h,json={'confirm':'RESUME PAPER BOT'})
    assert resumed.status_code == 200 and resumed.json()['worker']['armed']
    failed=client.post(url+'/close/SPY261106C00650000',headers=h,json={'confirm':'no'})
    assert failed.status_code == 400 and ctl.worker_status()['armed']
    closed=client.post(url+'/close/SPY261106C00650000',headers=h,json={'confirm':'CLOSE SPY261106C00650000'})
    assert closed.status_code == 200 and not closed.json()['worker']['armed']
    assert data['positions']==[]
    data['positions']=[{'symbol':'SPY261106P00645000','qty':'1'}]
    flat=client.post(url+'/close-all',headers=h,json={'confirm':'CLOSE ALL PAPER POSITIONS'})
    assert flat.status_code == 200 and flat.json()['all_requests_accepted']
    assert ('DELETE','/v2/positions','cancel_orders=true') in calls


def test_parse_rejects_changed_worker_format():
    from fastapi import HTTPException
    import pytest
    with pytest.raises(HTTPException) as err:
        ctl.parse_worker_status('worker no status')
    assert err.value.status_code == 503


def test_dashboard_wrapper_preserves_existing_page(tmp_path, monkeypatch):
    import importlib.util, types
    existing = FastAPI()
    @existing.get('/api/snapshot')
    def snapshot(): return {'snapshot':'preserved'}
    monkeypatch.setitem(sys.modules, 'aion_live_dashboard', types.SimpleNamespace(app=existing))
    (tmp_path / 'static').mkdir()
    (tmp_path / 'static' / 'index.html').write_text('<html><body><h1>AION LOGO PRESERVED</h1></body></html>')
    (tmp_path / 'aion_alpaca_controls.js').write_text('/* visual UI script */')
    spec = importlib.util.spec_from_file_location('aion_ltm_with_controls_test', Path(__file__).parent / 'aion_ltm_with_controls.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module,'ROOT',tmp_path)
    client=TestClient(module.app)
    page=client.get('/').text
    assert 'AION LOGO PRESERVED' in page
    assert page.count('/aion-alpaca-controls.js')==1
    assert client.get('/api/snapshot').json()=={'snapshot':'preserved'}
    assert client.get('/aion-alpaca-controls.js').status_code==200


def test_worker_arm_patch_guard(monkeypatch):
    from install_ltm_worker_arm_patch import patched_source
    before = "import re\nimport sys\nimport os\n\ndef foo():\n        if not sys.stdin.isatty():raise RuntimeError('Arm requires an interactive terminal')\n"
    result = patched_source(before)
    assert 'AION_LTM_ARM_APPROVAL' in result
    assert 'import secrets' in result
    assert patched_source(result) == result