"""AION KAIROS V5 signal receiver: local-only, NO BROKER ORDER EXECUTION.

Receives webhook signals and records signal intent in SQLite. It never imports or
calls a broker trading endpoint and deliberately refuses non-SIMULATION mode.
Do NOT expose this unprotected process to the public internet.
"""
from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, time, timezone
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")

MODE = os.getenv("AION_MODE", "SIMULATION").upper()
if MODE != "SIMULATION":
    raise RuntimeError("LIVE ORDER ROUTING IS NOT IMPLEMENTED. AION_MODE must be SIMULATION.")

# Strict V5-only guard: reject an older .env instead of silently listening to V4.1.2.
REQUIRED_STRATEGY = "KAIROS_V5"
ALLOWED_STRATEGY = os.getenv("AION_ALLOWED_STRATEGY", REQUIRED_STRATEGY).strip()
if ALLOWED_STRATEGY != REQUIRED_STRATEGY:
    raise RuntimeError(
        "AION KAIROS V5 only accepts KAIROS_V5. "
        "Set AION_ALLOWED_STRATEGY=KAIROS_V5 in .env and restart."
    )
MAX_SIGNAL_AGE_SECONDS = int(os.getenv("AION_MAX_SIGNAL_AGE_SECONDS", "180"))
ENFORCE_SESSION = os.getenv("AION_ENFORCE_SESSION", "false").strip().lower() == "true"
DB = ROOT / "aion_kairos_v5_signals.sqlite3"
NY = ZoneInfo("America/New_York")

# No broker credentials are loaded or used by this server.
# An account ID is present to ensure configuration has been completed, but never sent.
if not os.getenv("SNAPTRADE_CASH_ACCOUNT_ID"):
    raise RuntimeError("SNAPTRADE_CASH_ACCOUNT_ID missing from .env")

app = FastAPI(title="AION KAIROS V5 — Signal Simulator", docs_url=None, redoc_url=None, openapi_url=None)


class KairosSignal(BaseModel):
    model_config = ConfigDict(extra="forbid")
    event_id: str = Field(min_length=8, max_length=128, pattern=r"^[A-Za-z0-9_.:-]+$")
    strategy: str = Field(min_length=1, max_length=80)
    symbol: str = Field(min_length=1, max_length=24)
    timeframe: str
    signal: Literal["BUY_CALL", "BUY_PUT", "EXIT"]
    emitted_at: datetime
    underlying_price: float | None = Field(default=None, gt=0)

    @field_validator("symbol")
    @classmethod
    def normalize_symbol(cls, v: str) -> str:
        return v.strip().upper()

    @field_validator("timeframe")
    @classmethod
    def normalize_interval(cls, v: str) -> str:
        v = v.strip().lower()
        if v not in ("3", "3m"):
            raise ValueError("KAIROS requires a 3-minute timeframe")
        return "3m"

    @field_validator("emitted_at")
    @classmethod
    def require_tz(cls, v: datetime) -> datetime:
        if v.utcoffset() is None:
            raise ValueError("emitted_at must include timezone, e.g. Z")
        return v


def init_db() -> None:
    with sqlite3.connect(DB, timeout=10) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("""CREATE TABLE IF NOT EXISTS events (
            event_id TEXT PRIMARY KEY,
            received_at TEXT NOT NULL,
            strategy TEXT NOT NULL,
            symbol TEXT NOT NULL,
            signal TEXT NOT NULL,
            result TEXT NOT NULL,
            intent_state TEXT NOT NULL,
            details TEXT NOT NULL DEFAULT ''
        )""")
        conn.execute("""CREATE TABLE IF NOT EXISTS state (
            id INTEGER PRIMARY KEY CHECK(id = 1),
            intent_state TEXT NOT NULL
        )""")
        conn.execute("INSERT OR IGNORE INTO state(id, intent_state) VALUES (1, 'FLAT')")


init_db()


def check_time(signal: KairosSignal) -> None:
    now = datetime.now(timezone.utc)
    lag = (now - signal.emitted_at.astimezone(timezone.utc)).total_seconds()
    if lag < -15 or lag > MAX_SIGNAL_AGE_SECONDS:
        raise HTTPException(422, "Signal timestamp is future-dated or stale")
    if not ENFORCE_SESSION:
        return  # For local testing only; production must enforce session and exchange calendar.
    ny = now.astimezone(NY)
    if ny.weekday() >= 5:
        raise HTTPException(422, "Outside weekday trading session")
    clock = ny.time()
    if signal.signal.startswith("BUY") and not (time(9, 30) <= clock < time(14, 45)):
        raise HTTPException(422, "Entry blocked outside 09:30–14:45 ET")
    if signal.signal == "EXIT" and not (time(9, 30) <= clock < time(15, 0)):
        raise HTTPException(422, "EXIT signal received outside configured time window")


@app.get("/health")
def health():
    return {"service": "AION-KAIROS-V5", "strategy": REQUIRED_STRATEGY, "mode": MODE, "broker_orders_enabled": False,
            "session_enforced": ENFORCE_SESSION, "selected_account": "CASH (ID hidden)"}


@app.post("/webhook")
def webhook(signal: KairosSignal):
    if signal.strategy != ALLOWED_STRATEGY:
        raise HTTPException(422, "Only KAIROS_V5 signals are accepted")
    if signal.symbol not in ("SPY", "AMEX:SPY"):
        raise HTTPException(422, "Only SPY is permitted")
    check_time(signal)

    now = datetime.now(timezone.utc).isoformat()
    # BEGIN IMMEDIATE provides atomic state check + event insertion.
    with sqlite3.connect(DB, timeout=10, isolation_level=None) as conn:
        conn.execute("BEGIN IMMEDIATE")
        prior = conn.execute("SELECT result, intent_state FROM events WHERE event_id=?",
                             (signal.event_id,)).fetchone()
        if prior is not None:
            conn.execute("COMMIT")
            return {"result": "DUPLICATE", "intent_state": prior[1],
                    "broker_order_sent": False}

        prev = conn.execute("SELECT intent_state FROM state WHERE id=1").fetchone()[0]
        direction = {"BUY_CALL": "CALL_INTENT", "BUY_PUT": "PUT_INTENT"}
        if signal.signal in direction:
            if prev == "FLAT":
                next_state = direction[signal.signal]
                outcome, detail = "SIGNAL_LOGGED", "No option contract or fill has been simulated"
            elif prev == direction[signal.signal]:
                next_state = prev
                outcome, detail = "IGNORED_ALREADY_SIGNALED", "Repeated direction"
            else:
                next_state = prev
                outcome, detail = "BLOCKED_OPPOSING_INTENT", "An EXIT must arrive first"
        else:
            next_state = "FLAT"
            outcome, detail = ("EXIT_INTENT_LOGGED", "No broker position was closed") if prev != "FLAT" else ("IGNORED_ALREADY_FLAT", "No active signal intent")

        conn.execute("""INSERT INTO events
            (event_id, received_at, strategy, symbol, signal, result, intent_state, details)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (signal.event_id, now, signal.strategy, signal.symbol, signal.signal, outcome, next_state, detail))
        conn.execute("UPDATE state SET intent_state=? WHERE id=1", (next_state,))
        conn.execute("COMMIT")
    return {"result": outcome, "intent_state": next_state,
            "broker_order_sent": False, "mode": MODE}


@app.get("/status")
def status():
    with sqlite3.connect(DB, timeout=10) as conn:
        current = conn.execute("SELECT intent_state FROM state WHERE id=1").fetchone()[0]
        count = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    return {"strategy": REQUIRED_STRATEGY, "intent_state": current, "signals_logged": count,
            "actual_options_position": "UNKNOWN — NO BROKER TRADING IN THIS APP"}


@app.get("/events")
def recent_events(limit: int = Query(default=10, ge=1, le=50)):
    with sqlite3.connect(DB, timeout=10) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT received_at, strategy, symbol, signal, result, intent_state "
                            "FROM events ORDER BY received_at DESC LIMIT ?", (limit,)).fetchall()
    return {"events": [dict(r) for r in rows]}
