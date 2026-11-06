"""No network, credentials or broker calls. Supervisor failure/lock regression tests."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import aion_paper_supervisor as sup
import aion_alpaca_control as ctl


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    mapping = {
        'ROOT': tmp_path, 'DB': tmp_path / 'state.sqlite3',
        'WORKER': tmp_path / 'aion_alpaca_paper_algo.py',
        'KILL': tmp_path / 'AION_PAPER_KILL_SWITCH.txt',
        'WORKER_LOCK': tmp_path / '.worker.lock',
        'SUPERVISOR_LOCK': tmp_path / '.supervisor.lock',
        'STATUS': tmp_path / '.supervisor_status.json',
        'LOG_DIR': tmp_path / 'logs',
        'WORKER_LOG': tmp_path / 'logs' / 'worker.log',
        'SUPERVISOR_LOG': tmp_path / 'logs' / 'supervisor.log',
    }
    for key, value in mapping.items():
        monkeypatch.setattr(sup, key, value)
    mapping['WORKER'].write_text('# worker exists\n', encoding='utf-8')
    with sqlite3.connect(mapping['DB']) as db:
        db.execute('CREATE TABLE cfg(name TEXT PRIMARY KEY,value TEXT NOT NULL)')
    def set_state(armed=False, generation='session1', bound=True):
        items = [('armed', 'true' if armed else 'false'),
                 ('armed_at', generation),
                 ('paper_account_id', 'test-account' if bound else '')]
        with sqlite3.connect(mapping['DB']) as db:
            db.executemany('INSERT OR REPLACE INTO cfg VALUES (?,?)', items)
    mapping['set_state'] = set_state
    set_state()
    return mapping


def test_supervisor_does_not_arm_or_launch_if_disarmed(sandbox):
    spawned=[]
    sup.run_loop(max_ticks=3, sleep=lambda _:None, spawn=lambda:spawned.append(1))
    assert spawned == []
    result=json.loads(sandbox['STATUS'].read_text())
    assert result['mode']=='WAITING'
    assert result['armed'] is False


def test_supervisor_starts_one_worker_only_when_armed(sandbox):
    sandbox['set_state'](armed=True)
    class Child:
        pid=2311
        def poll(self): return None
    spawned=[]
    def launch():
        spawned.append(Child())
        return spawned[-1]
    sup.run_loop(max_ticks=3,sleep=lambda _:None,spawn=launch)
    assert len(spawned)==1
    result=json.loads(sandbox['STATUS'].read_text())
    assert result['mode']=='RUNNING'
    assert result['worker_pid']==2311
    assert not sandbox['KILL'].exists()


def test_external_worker_not_taken_over_after_it_stops(sandbox, monkeypatch):
    sandbox['set_state'](armed=True)
    calls=iter([True,False,False])
    monkeypatch.setattr(sup,'external_worker_running',lambda:next(calls))
    launched=[]
    sup.run_loop(max_ticks=3,sleep=lambda _:None,spawn=lambda:launched.append(True))
    assert launched==[]
    assert json.loads(sandbox['STATUS'].read_text())['mode']=='WAITING_OPERATOR'


def test_unexpected_worker_exit_sets_existing_kill_switch_no_restart(sandbox):
    sandbox['set_state'](armed=True)
    class Child:
        pid=42
        def poll(self): return 1
    launched=[]
    def launch():
        launched.append(Child())
        return launched[-1]
    sup.run_loop(max_ticks=4,sleep=lambda _:None,spawn=launch)
    assert len(launched)==1
    assert sandbox['KILL'].exists()
    assert 'supervisor halted' in sandbox['KILL'].read_text().lower()
    assert json.loads(sandbox['STATUS'].read_text())['mode']=='WAITING'
    assert sup.ready(sup.read_arm_state()) is False


def test_pause_then_resumption_requires_new_arming_generation(sandbox, monkeypatch):
    sandbox['set_state'](armed=True,generation='start')
    class Child:
        pid=88
        def __init__(self): self.stopped=False
        def poll(self): return 0 if self.stopped else None
    old=Child()
    launched=[]
    def launch():
        launched.append(old if not launched else Child())
        return launched[-1]
    def sleep(_):
        if len(launched)==1 and not old.stopped:
            sandbox['set_state'](armed=False,generation='start')
            old.stopped=True
        elif old.stopped and len(launched)==1:
            sandbox['set_state'](armed=True,generation='restart')
    sup.run_loop(max_ticks=4,sleep=sleep,spawn=launch)
    assert len(launched)==2
    assert not sandbox['KILL'].exists()


def test_supervisor_lock_blocks_duplicate_instances(sandbox):
    with sup.single_supervisor():
        with pytest.raises(RuntimeError,match='Another AION PAPER supervisor'):
            with sup.single_supervisor():
                pass


def test_dashboard_supervisor_status_fresh_and_stale(tmp_path,monkeypatch):
    path=tmp_path/'status.json'
    monkeypatch.setattr(ctl,'SUPERVISOR_STATUS',path)
    path.write_text(json.dumps({'updated_at':datetime.now(timezone.utc).isoformat(),
                                'mode':'RUNNING','worker_pid':111}),encoding='utf-8')
    result=ctl.supervisor_status()
    assert result['online'] is True and result['mode']=='RUNNING'
    path.write_text(json.dumps({'updated_at':(datetime.now(timezone.utc)-timedelta(minutes=5)).isoformat(),
                                'mode':'RUNNING'}),encoding='utf-8')
    assert ctl.supervisor_status()['online'] is False


def test_dashboard_resume_requests_task_when_supervisor_offline(monkeypatch):
    from fastapi.testclient import TestClient
    from fastapi import FastAPI
    monkeypatch.setattr(ctl,'authenticate',lambda *args,**kwargs:None)
    monkeypatch.setattr(ctl,'ensure_bound',lambda:{'account_bound':True,'armed':False,'kill_switch':True,'worker_recent':False})
    monkeypatch.setattr(ctl,'worker_command',lambda command:{'account_bound':True,'armed':True,'kill_switch':False,'worker_recent':False})
    monkeypatch.setattr(ctl,'supervisor_status',lambda:{'online':False,'mode':'OFFLINE'})
    calls=[]
    monkeypatch.setattr(ctl,'request_supervisor_start',lambda:calls.append('start') or 'Windows task launched')
    app=FastAPI();app.include_router(ctl.ROUTER)
    r=TestClient(app).post('/api/alpaca-control/resume',json={'confirm':'RESUME PAPER BOT'})
    assert r.status_code==200
    assert calls==['start']
    assert 'Windows task launched' in r.json()['message']