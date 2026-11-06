"""Independent AION Alpaca PAPER worker supervisor for Windows Task Scheduler.

The supervisor never arms a bot, never calls Alpaca and never submits an order.
It starts the existing PAPER-only worker only while explicitly armed, account-bound,
and the kill switch is clear. An unexpected worker exit is FAIL-CLOSED: the
supervisor activates the existing kill switch and requires manual reconciliation.

Task Scheduler starts this program at Windows user logon (not as SYSTEM).
All status/log files remain local and must never be committed to Git.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import sys
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
WORKER = ROOT / 'aion_alpaca_paper_algo.py'
DB = ROOT / 'aion_alpaca_paper_algo.sqlite3'
KILL = ROOT / 'AION_PAPER_KILL_SWITCH.txt'
WORKER_LOCK = ROOT / '.aion_alpaca_paper_worker.lock'
SUPERVISOR_LOCK = ROOT / '.aion_alpaca_paper_supervisor.lock'
STATUS = ROOT / '.aion_paper_supervisor_status.json'
LOG_DIR = ROOT / 'logs'
WORKER_LOG = LOG_DIR / 'aion-paper-worker-supervised.log'
SUPERVISOR_LOG = LOG_DIR / 'aion-paper-supervisor.log'
LOOP_SECONDS = 3


class SupervisorAlreadyRunning(RuntimeError):
    """A healthy supervisor already holds the lock; do not trip a kill switch."""



def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_arm_state() -> dict:
    """Read existing durable arming state without changing it or creating a DB."""
    values: dict[str, str] = {}
    if DB.is_file():
        try:
            with sqlite3.connect(f'file:{DB.as_posix()}?mode=ro', uri=True, timeout=2) as db:
                values = {str(k): str(v) for k, v in db.execute(
                    "SELECT name, value FROM cfg WHERE name IN ('armed','armed_at','paper_account_id')")}
        except (sqlite3.Error, OSError):
            values = {}
    return {
        'armed': values.get('armed') == 'true',
        'generation': values.get('armed_at', ''),
        'account_bound': bool(values.get('paper_account_id')),
        'kill_switch': KILL.exists(),
    }


def ready(state: dict) -> bool:
    return bool(state['armed'] and state['account_bound'] and not state['kill_switch'] and WORKER.is_file())


def _lock_one_byte(path: Path):
    """Acquire an exclusive non-blocking OS lock; returns an open handle."""
    path.touch(exist_ok=True)
    handle = open(path, 'a+b')
    try:
        if sys.platform == 'win32':
            import msvcrt
            handle.seek(0)
            if not handle.read(1):
                handle.seek(0)
                handle.write(b'0')
                handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (OSError, BlockingIOError):
        handle.close()
        return None
    return handle


def _unlock_one_byte(handle) -> None:
    try:
        if sys.platform == 'win32':
            import msvcrt
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    finally:
        handle.close()


@contextmanager
def single_supervisor():
    handle = _lock_one_byte(SUPERVISOR_LOCK)
    if handle is None:
        raise SupervisorAlreadyRunning('Another AION PAPER supervisor is already running')
    try:
        yield
    finally:
        _unlock_one_byte(handle)


def external_worker_running() -> bool:
    """Uses the worker's own lock, so a manual worker is never duplicated."""
    handle = _lock_one_byte(WORKER_LOCK)
    if handle is None:
        return True
    _unlock_one_byte(handle)
    return False


def write_status(mode: str, *, child_pid: int | None = None, reason: str = '',
                 last_exit: int | None = None, state: dict | None = None) -> None:
    state = read_arm_state() if state is None else state
    payload = {
        'schema': 1, 'supervisor_pid': os.getpid(), 'updated_at': now(),
        'mode': mode, 'worker_pid': child_pid, 'reason': reason[:180],
        'last_worker_exit_code': last_exit, 'armed': state['armed'],
        'account_bound': state['account_bound'], 'kill_switch': state['kill_switch'],
    }
    temp = STATUS.with_name(STATUS.name + f'.{os.getpid()}.tmp')
    temp.write_text(json.dumps(payload, separators=(',', ':')), encoding='utf-8')
    os.replace(temp, STATUS)


def append_supervisor_log(message: str) -> None:
    LOG_DIR.mkdir(exist_ok=True)
    # Generic reason only: NEVER log tokens, broker credentials or full subprocess env.
    with open(SUPERVISOR_LOG, 'a', encoding='utf-8') as handle:
        handle.write(f'{now()} {message}\n')


def spawn_worker() -> subprocess.Popen:
    python = ROOT / '.venv' / 'Scripts' / 'python.exe'
    if not python.is_file():
        # Test/development path only; Windows production must use its own venv.
        if sys.platform == 'win32':
            raise RuntimeError('Missing .venv\\Scripts\\python.exe')
        python = Path(sys.executable)
    LOG_DIR.mkdir(exist_ok=True)
    # Entire stdout/stderr stays local; no token or key is printed by the supervisor.
    with open(WORKER_LOG, 'ab', buffering=0) as output:
        return subprocess.Popen(
            [str(python), '-u', str(WORKER), 'run'], cwd=str(ROOT),
            stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
            close_fds=True,
        )


def fail_closed(reason: str) -> None:
    """Stop future broker execution until manual account/order reconciliation."""
    # If the operator already paused, preserve their existing kill-switch reason.
    if not KILL.exists():
        KILL.write_text('PAPER supervisor halted after unexpected worker exit. '
                        'Reconcile Alpaca positions/orders and manually Resume.\n', encoding='utf-8')
    append_supervisor_log(reason)


def run_loop(*, sleep=time.sleep, spawn=spawn_worker, max_ticks: int | None = None) -> None:
    """Do not restart a failed worker inside the same arming generation."""
    child = None
    started_generation = ''
    previous_exit = None
    external_generation = None
    tick = 0
    with single_supervisor():
        append_supervisor_log('supervisor started (PAPER only; no automatic arming)')
        while max_ticks is None or tick < max_ticks:
            tick += 1
            state = read_arm_state()
            if child is not None:
                code = child.poll()
                if code is None:
                    write_status('RUNNING' if ready(state) else 'STOPPING',
                                 child_pid=child.pid, reason='Worker exiting after disarm' if not ready(state) else '',
                                 last_exit=previous_exit, state=state)
                else:
                    previous_exit = int(code)
                    child = None
                    # Pause/kill or a *new* arming generation makes this a normal old-session exit.
                    if ready(state) and state['generation'] == started_generation:
                        fail_closed(f'worker unexpectedly exited (code {code}); restart blocked until operator re-arms')
                        state = read_arm_state()
                        write_status('HALTED', reason='Worker exited unexpectedly; verify PAPER orders',
                                     last_exit=previous_exit, state=state)
                    else:
                        write_status('WAITING', reason='Previous worker exited',
                                     last_exit=previous_exit, state=state)
            else:
                if not ready(state):
                    reason = ('Kill switch set; requires operator review' if state['kill_switch']
                              else 'Awaiting explicit arming / account binding')
                    write_status('WAITING', reason=reason, last_exit=previous_exit, state=state)
                elif external_worker_running():
                    # Never take over an already-running manual worker after it stops.
                    external_generation = state['generation']
                    write_status('EXTERNAL_WORKER', reason='Manual worker holds execution lock; no automatic takeover',
                                 last_exit=previous_exit, state=state)
                elif external_generation == state['generation']:
                    write_status('WAITING_OPERATOR', reason='Manual worker stopped; Pause/Resume to hand off to supervisor',
                                 last_exit=previous_exit, state=state)
                else:
                    try:
                        child = spawn()
                        started_generation = state['generation']
                        append_supervisor_log('worker started under supervised PAPER session')
                        write_status('STARTING', child_pid=child.pid, last_exit=previous_exit, state=state)
                    except (OSError, RuntimeError, ValueError) as exc:
                        # Never repeatedly spawn a broken/unavailable Python worker.
                        fail_closed('worker launch failed: ' + type(exc).__name__)
                        state = read_arm_state()
                        write_status('HALTED', reason='Worker launch failure; inspect local supervisor log',
                                     last_exit=previous_exit, state=state)
            sleep(LOOP_SECONDS)


def cli() -> None:
    parser = argparse.ArgumentParser(description='AION Alpaca PAPER-only Windows supervisor')
    parser.add_argument('command', nargs='?', choices=['run', 'status'], default='run')
    args = parser.parse_args()
    if args.command == 'status':
        try:
            payload = json.loads(STATUS.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            payload = {'mode': 'NOT_INSTALLED_OR_OFFLINE'}
        payload['saved_bot_state'] = read_arm_state()
        print(json.dumps(payload, indent=2))
    else:
        try:
            run_loop()
        except SupervisorAlreadyRunning:
            # A second task launch is not a worker crash. Existing supervisor stays in charge.
            return
        except Exception as exc:
            # The task must not automatically respawn after a broken supervisor.
            try:
                fail_closed('supervisor unexpectedly stopped: ' + type(exc).__name__)
                write_status('HALTED', reason='Supervisor failure; inspect local log')
            finally:
                raise


if __name__ == '__main__':
    cli()