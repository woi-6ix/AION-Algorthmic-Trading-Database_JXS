"""Read-only display source for AION option execution fills.

The dashboard never submits brokerage orders. A FUTURE execution/reconciliation
worker may call record_fill *after* receiving a confirmed broker/paper fill.
"""
from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
DEFAULT_DB = HERE / "aion_execution_journal.sqlite3"
OCC_PATTERN = re.compile(r"^[A-Z0-9 ]{6}\d{6}[CP]\d{8}$")


def connect(db_path: Path = DEFAULT_DB) -> sqlite3.Connection:
    con = sqlite3.connect(str(db_path), timeout=5)
    con.row_factory = sqlite3.Row
    return con


def ensure_db(db_path: Path = DEFAULT_DB) -> None:
    with connect(db_path) as con:
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("""CREATE TABLE IF NOT EXISTS option_fills (
            fill_id TEXT PRIMARY KEY,
            filled_at TEXT NOT NULL,
            strategy TEXT NOT NULL,
            symbol TEXT NOT NULL,
            option_symbol TEXT NOT NULL,
            mode TEXT NOT NULL CHECK (mode IN ('PAPER','LIVE')),
            action TEXT NOT NULL CHECK (action IN ('BUY_TO_OPEN','SELL_TO_CLOSE')),
            contracts INTEGER NOT NULL CHECK (contracts > 0),
            fill_price REAL NOT NULL CHECK (fill_price > 0),
            fees_usd REAL NOT NULL CHECK (fees_usd >= 0),
            source TEXT NOT NULL,
            broker_order_id TEXT NOT NULL DEFAULT ''
        )""")


def record_fill(*, fill_id: str, filled_at: str, option_symbol: str, mode: str,
                action: str, contracts: int, fill_price: float, fees_usd: float = 0,
                source: str, broker_order_id: str = "", db_path: Path = DEFAULT_DB) -> bool:
    """Idempotently store a *confirmed* paper or broker fill. Returns True on insert.

    IMPORTANT: call only after fill confirmation, never directly from TradingView
    signals, order-impact previews, or order-submission acknowledgements.
    """
    timestamp = datetime.fromisoformat(filled_at.replace("Z", "+00:00"))
    if timestamp.tzinfo is None:
        raise ValueError("filled_at must include timezone")
    timestamp = timestamp.astimezone(timezone.utc).isoformat()
    option_symbol = option_symbol.upper().replace("SPY ", "SPY   ") if option_symbol.startswith("SPY ") and len(option_symbol) == 19 else option_symbol.upper()
    if not OCC_PATTERN.fullmatch(option_symbol) or not option_symbol.startswith("SPY   "):
        raise ValueError("Only OCC-formatted SPY option symbols are allowed")
    if mode not in {"PAPER", "LIVE"} or action not in {"BUY_TO_OPEN", "SELL_TO_CLOSE"}:
        raise ValueError("Unsupported fill mode or action")
    if not isinstance(contracts, int) or isinstance(contracts, bool) or contracts <= 0 or not 0 < float(fill_price) < 1_000_000 or not 0 <= float(fees_usd) < 1_000_000:
        raise ValueError("Invalid quantity, fill premium or fees")
    if not fill_id or len(fill_id) > 128 or not source or len(source) > 100:
        raise ValueError("Fill must have a valid unique ID and source")
    ensure_db(db_path)
    with connect(db_path) as con:
        cur = con.execute("""INSERT OR IGNORE INTO option_fills
        (fill_id, filled_at, strategy, symbol, option_symbol, mode, action, contracts, fill_price, fees_usd, source, broker_order_id)
        VALUES (?, ?, 'KAIROS_V5', 'SPY', ?, ?, ?, ?, ?, ?, ?, ?)""",
        (fill_id, timestamp, option_symbol, mode, action, contracts, float(fill_price), float(fees_usd), source, broker_order_id))
        return cur.rowcount == 1


def read_fills(db_path: Path = DEFAULT_DB, limit: int = 300) -> list[dict[str, Any]]:
    ensure_db(db_path)
    with connect(db_path) as con:
        rows = con.execute("""SELECT fill_id, filled_at, strategy, symbol, option_symbol, mode, action,
                contracts, fill_price, fees_usd, source, broker_order_id
                FROM option_fills ORDER BY filled_at DESC, fill_id DESC LIMIT ?""", (min(500, max(1, limit)),)).fetchall()
    return [dict(r) for r in rows]


def summarize_fills(fills: list[dict[str, Any]]) -> dict[str, Any]:
    """FIFO realized P&L for displayed fills, including per-fill fees.

    For the current small local journal, callers should request all fills up to
    the cap. P&L is marked incomplete if >500 fills are needed.
    """
    inventory: dict[tuple[str, str], list[dict[str, float]]] = {}
    realized = {"PAPER": 0.0, "LIVE": 0.0}
    closings = {"PAPER": 0, "LIVE": 0}
    unmatched_exit = 0
    for fill in sorted(fills, key=lambda x: (x["filled_at"], x["fill_id"])):
        mode = fill["mode"]
        key = (mode, fill["option_symbol"])
        lots = inventory.setdefault(key, [])
        qty = int(fill["contracts"])
        price = float(fill["fill_price"])
        fees = float(fill["fees_usd"])
        if fill["action"] == "BUY_TO_OPEN":
            lots.append({"qty": qty, "price": price, "entry_fee_each": fees / qty})
        else:
            remaining = qty
            while remaining and lots:
                lot = lots[0]
                matched = min(remaining, int(lot["qty"]))
                realized[mode] += matched * (price - lot["price"]) * 100 - matched * (lot["entry_fee_each"] + fees / qty)
                lot["qty"] -= matched
                remaining -= matched
                if lot["qty"] == 0:
                    lots.pop(0)
            if remaining:
                unmatched_exit += remaining
            closings[mode] += 1
    open_contracts = {m: sum(int(lot["qty"]) for (mode, _), lots in inventory.items() if mode == m for lot in lots) for m in ("PAPER", "LIVE")}
    return {"realized_pnl_usd": {mode: round(realized[mode], 2) for mode in realized},
            "close_fills": closings, "open_contracts": open_contracts,
            "unmatched_exit_contracts": unmatched_exit,
            "fills_recorded": len(fills), "pnl_may_be_incomplete": len(fills) >= 500}
