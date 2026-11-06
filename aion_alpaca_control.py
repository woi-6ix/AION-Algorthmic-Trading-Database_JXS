"""Opt-in Alpaca PAPER trade controls for AION LTM.

No real-money endpoints; no broker credentials returned to clients.
The existing AION Alpaca paper worker CLI remains the authority for arming.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import Request, urlopen

from fastapi import APIRouter, HTTPException, Request as HTTPRequest
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parent
PAPER_ENDPOINT = "https://paper-api.alpaca.markets"
CLI_NAME = "aion_alpaca_paper_algo.py"
MUTATION_LOCK = threading.RLock()
ROUTER = APIRouter(prefix="/api/alpaca-control", tags=["alpaca-paper-controls"])


class Confirmation(BaseModel):
    confirm: str


def fail(status: int, message: str):
    raise HTTPException(status_code=status, detail=message)


def authenticate(request: HTTPRequest, *, mutating: bool = False) -> None:
    expected = os.getenv("AION_CONTROL_TOKEN", "").strip()
    if len(expected) < 24:
        fail(503, "Control token not configured. Set AION_CONTROL_TOKEN (24+ characters) on the dashboard server.")
    received = request.headers.get("X-Aion-Control-Token", "")
    if not secrets.compare_digest(received, expected):
        fail(401, "Invalid control token")
    # Defense in depth against cross-origin requests to the Tailnet-served UI.
    if mutating:
        origin = request.headers.get("origin")
        if origin:
            origin_info = urlsplit(origin)
            if origin_info.scheme not in {"https", "http"} or origin_info.netloc.lower() != request.headers.get("host", "").lower():
                fail(403, "Cross-origin control requests are blocked")


def env_values() -> dict[str, str]:
    """Read only credentials from the user's local .env; never expose values."""
    values = {}
    path = ROOT / ".env"
    if path.is_file():
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            name, value = line.split("=", 1)
            name = name.strip().removeprefix("export ").strip()
            value = value.strip()
            if value and value[0] in {"'", '"'} and value[-1] == value[0]:
                value = value[1:-1]
            values[name] = value
    # Environment always wins over .env for explicit overrides.
    values.update(os.environ)
    return values


def paper_keys() -> tuple[str, str]:
    config = env_values()
    for k, s in (
        ("ALPACA_PAPER_API_KEY", "ALPACA_PAPER_API_SECRET"),
        ("ALPACA_PAPER_KEY_ID", "ALPACA_PAPER_SECRET_KEY"),
        ("APCA_API_KEY_ID", "APCA_API_SECRET_KEY"),
        ("ALPACA_API_KEY", "ALPACA_SECRET_KEY"),
        ("ALPACA_API_KEY_ID", "ALPACA_API_SECRET_KEY"),
    ):
        if config.get(k) and config.get(s):
            return config[k], config[s]
    fail(503, "Alpaca PAPER credentials not available in the server environment or .env")


def broker(method: str, path: str, *, query: str = ""):
    key, secret = paper_keys()
    url = PAPER_ENDPOINT + path + (("?" + query) if query else "")
    request = Request(
        url, method=method,
        headers={
            "APCA-API-KEY-ID": key,
            "APCA-API-SECRET-KEY": secret,
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "AION-LTM-PaperControls/1.0",
        },
    )
    try:
        with urlopen(request, timeout=12) as response:
            raw = response.read(1_000_000)
            body = json.loads(raw) if raw else None
            return response.status, body
    except HTTPError as exc:
        # Alpaca errors should be useful but must never include request secrets.
        try:
            detail = json.loads(exc.read(8_000).decode("utf-8"))
            if isinstance(detail, dict):
                detail = detail.get("message", "Broker request failed")
        except Exception:
            detail = "Broker request failed"
        fail(502, f"Alpaca PAPER API returned HTTP {exc.code}: {str(detail)[:300]}")
    except (URLError, TimeoutError, OSError, ValueError):
        fail(502, "Alpaca PAPER API unavailable or returned invalid data")


def worker_status():
    path = ROOT / CLI_NAME
    if not path.is_file():
        fail(503, f"Missing {CLI_NAME} in AION-Execution directory; controls cannot safely affect the trading worker")
    try:
        completed = subprocess.run(
            [sys.executable, str(path), "status"],
            cwd=str(ROOT), capture_output=True, text=True, timeout=12, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        fail(503, "Unable to query Alpaca PAPER worker status")
    if completed.returncode != 0:
        fail(503, "Alpaca PAPER worker status command failed")
    return parse_worker_status(completed.stdout)


def parse_worker_status(output: str) -> dict:
    match = re.search(r"Paper bot armed:\s*(True|False)\s*\|\s*Kill switch:\s*(True|False)\s*\|\s*Account bound:\s*(True|False)", output, re.I)
    if not match:
        fail(503, "Worker status output was not recognized; controls are blocked until integration is verified")
    heartbeat_match = re.search(r"Last heartbeat:\s*([^\r\n]+)", output, re.I)
    heartbeat = heartbeat_match.group(1).strip() if heartbeat_match else None
    fresh = False
    if heartbeat:
        try:
            dt = datetime.fromisoformat(heartbeat.replace("Z", "+00:00"))
            fresh = dt.tzinfo is not None and -15 <= (datetime.now(timezone.utc) - dt).total_seconds() <= 90
        except ValueError:
            pass
    return {"armed": match.group(1).lower() == "true",
            "kill_switch": match.group(2).lower() == "true",
            "account_bound": match.group(3).lower() == "true",
            "last_heartbeat": heartbeat, "worker_recent": fresh}


def worker_command(command: str) -> dict:
    if command not in {"arm", "disarm"}:
        fail(500, "Unsupported worker command")
    path = ROOT / CLI_NAME
    if not path.is_file():
        fail(503, "Alpaca PAPER worker executable is missing")
    # The user's known CLI arm operation prompts for this exact confirmation.
    input_text = "ARM ALPACA PAPER\n" if command == "arm" else ""
    child_env = os.environ.copy()
    if command == "arm":
        # The worker independently checks this short-lived authorization against
        # AION_CONTROL_TOKEN; ordinary noninteractive CLI arming stays forbidden.
        child_env["AION_LTM_ARM_APPROVAL"] = os.getenv("AION_CONTROL_TOKEN", "")
    try:
        result = subprocess.run(
            [sys.executable, str(path), command], cwd=str(ROOT),
            input=input_text, capture_output=True, text=True, timeout=15, check=False,
            env=child_env,
        )
    except (OSError, subprocess.TimeoutExpired):
        fail(503, f"Worker {command} command failed or timed out")
    if result.returncode != 0:
        fail(503, f"Worker rejected {command} command; no state change confirmed")
    state = worker_status()
    if command == "arm" and not (state["armed"] and not state["kill_switch"] and state["account_bound"]):
        fail(409, "Worker did not confirm an armed, bound PAPER account")
    if command == "disarm" and (state["armed"] or not state["kill_switch"]):
        fail(409, "Worker did not confirm disarmed + kill switch enabled")
    return state


def ensure_bound():
    state = worker_status()
    if not state["account_bound"]:
        fail(409, "Worker has not bound the Alpaca PAPER account")
    return state


def disarm_for_closure() -> dict:
    state = ensure_bound()
    if state["armed"] or not state["kill_switch"]:
        state = worker_command("disarm")
    if state["armed"] or not state["kill_switch"]:
        fail(409, "Bot could not be safely disarmed")
    return state


def positions():
    _, data = broker("GET", "/v2/positions")
    if not isinstance(data, list):
        fail(502, "Unexpected positions response from Alpaca")
    return data


def orders():
    _, data = broker("GET", "/v2/orders", query="status=open&limit=500&nested=true")
    if not isinstance(data, list) or len(data) >= 500:
        fail(502, "Could not verify all open orders; no liquidation submitted")
    return data


def cancel_orders_for_symbol(symbol: str):
    open_orders = orders()
    relevant = [x for x in open_orders if x.get("symbol") == symbol or any(
        leg.get("symbol") == symbol for leg in (x.get("legs") or []) if isinstance(leg, dict)
    )]
    for order in relevant:
        order_id = str(order.get("id", ""))
        if not re.fullmatch(r"[0-9a-fA-F-]{36}", order_id):
            fail(502, "Unrecognized open order ID; refused to submit a conflicting liquidation")
        try:
            broker("DELETE", "/v2/orders/" + order_id)
        except HTTPException as exc:
            if exc.status_code != 502:
                raise
            # An order can fill before a cancel arrives; verify after the race.
    for _ in range(5):
        remaining = [x for x in orders() if x.get("symbol") == symbol or any(
            leg.get("symbol") == symbol for leg in (x.get("legs") or []) if isinstance(leg, dict)
        )]
        if not remaining:
            return
        time.sleep(.35)
    fail(409, f"Open orders for {symbol} have not been canceled; position close was NOT submitted")


def check_confirm(received: str, expected: str):
    if not secrets.compare_digest(received, expected):
        fail(400, f"Confirmation must be exactly {expected}")


@ROUTER.get("/state")
def get_state(request: HTTPRequest):
    authenticate(request)
    state = worker_status()
    try:
        open_positions = positions()
        state["broker_connected"] = True
        state["broker_error"] = None
        state["positions"] = [{k: p.get(k) for k in (
            "symbol", "qty", "side", "asset_class", "market_value", "unrealized_pl", "unrealized_plpc",
            "avg_entry_price", "current_price")}
            for p in open_positions if isinstance(p, dict)]
    except HTTPException as exc:
        state["broker_connected"] = False
        state["broker_error"] = exc.detail
        state["positions"] = []
    state["account_mode"] = "PAPER ONLY"
    state["auto_trading_active"] = bool(state["armed"] and not state["kill_switch"] and state["account_bound"] and state["worker_recent"])
    return state


@ROUTER.post("/pause")
def pause_trading(request: HTTPRequest, payload: Confirmation):
    authenticate(request, mutating=True)
    check_confirm(payload.confirm, "PAUSE PAPER BOT")
    with MUTATION_LOCK:
        state = ensure_bound()
        if state["armed"] or not state["kill_switch"]:
            state = worker_command("disarm")
        return {"message": "PAPER bot disarmed. Existing positions remain open; automated exits may also be disabled.", "worker": state}


@ROUTER.post("/resume")
def resume_trading(request: HTTPRequest, payload: Confirmation):
    authenticate(request, mutating=True)
    check_confirm(payload.confirm, "RESUME PAPER BOT")
    with MUTATION_LOCK:
        state = ensure_bound()
        if not state["armed"] or state["kill_switch"]:
            state = worker_command("arm")
        message = ("PAPER bot armed and worker heartbeat recent. Automated entries can resume."
                   if state["worker_recent"] else
                   "PAPER bot armed, but worker heartbeat is stale; start/verify the existing worker before expecting trades.")
        return {"message": message, "worker": state}


@ROUTER.post("/close/{symbol}")
def close_one(symbol: str, request: HTTPRequest, payload: Confirmation):
    authenticate(request, mutating=True)
    symbol = symbol.upper().strip()
    if not re.fullmatch(r"[A-Z0-9._-]{1,40}", symbol):
        fail(400, "Invalid broker position symbol")
    check_confirm(payload.confirm, f"CLOSE {symbol}")
    with MUTATION_LOCK:
        state = disarm_for_closure()
        # Cancel any same-symbol pending entries/exits even if the displayed
        # position just disappeared, to avoid a stale UI leaving an order behind.
        cancel_orders_for_symbol(symbol)
        # Recheck the position after cancellations, since fills may change it.
        matching = [x for x in positions() if x.get("symbol") == symbol]
        if not matching:
            return {"message": "Position no longer open after canceling orders; no close submitted", "worker": state}
        _, order = broker("DELETE", "/v2/positions/" + quote(symbol, safe=""))
        return {"message": "PAPER position close order submitted (not necessarily filled). Bot remains DISARMED.",
                "worker": state,
                "close_order": {"id": order.get("id"), "status": order.get("status"), "symbol": order.get("symbol")}
                    if isinstance(order, dict) else None}


@ROUTER.post("/close-all")
def close_all(request: HTTPRequest, payload: Confirmation):
    authenticate(request, mutating=True)
    check_confirm(payload.confirm, "CLOSE ALL PAPER POSITIONS")
    with MUTATION_LOCK:
        state = disarm_for_closure()
        # This broker operation explicitly cancels ALL open orders in this PAPER
        # account before attempting liquidation of ALL PAPER positions.
        status, result = broker("DELETE", "/v2/positions", query="cancel_orders=true")
        outcomes = []
        if isinstance(result, list):
            for item in result:
                body = item.get("body") if isinstance(item, dict) else None
                outcomes.append({"symbol": item.get("symbol"), "status": item.get("status"),
                                 "order_id": body.get("id") if isinstance(body, dict) else None})
        return {"message": "PAPER account-wide close requested. Check per-position results and fills. Bot remains DISARMED.",
                "worker": state, "alpaca_http_status": status, "outcomes": outcomes,
                "all_requests_accepted": bool(outcomes) and all(item.get("status") == 200 for item in outcomes)}