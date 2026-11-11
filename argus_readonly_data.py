"""ARGUS read-only data access.

All SQLite connections use mode=ro + PRAGMA query_only; all HTTP calls use GET
to fixed loopback endpoints. Never import or invoke the broker execution worker.
No raw secrets, account IDs, webhook messages, notes, or full logs are returned.
"""
from __future__ import annotations

import json
import re
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent
WORKER_DB = "aion_alpaca_paper_algo.sqlite3"
SIGNAL_DB = "aion_kairos_v5_signals.sqlite3"
JOURNAL_DB = "aion_execution_journal.sqlite3"
SUPERVISOR_FILE = ".aion_paper_supervisor_status.json"
KILL_FILE = "AION_PAPER_KILL_SWITCH.txt"
MAX_ROWS = 50
MAX_HTTP_BYTES = 180_000
FRESHNESS_SECONDS = 45
ALLOWED_SYMBOL = re.compile(r"^[A-Z][A-Z0-9.^-]{0,9}$")


def utc_now():
    return datetime.now(timezone.utc)


def iso_now():
    return utc_now().isoformat()


def age_seconds(timestamp):
    if not timestamp:
        return None
    try:
        value = datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
        if value.tzinfo is None:
            return None
        return max(0, round((utc_now() - value.astimezone(timezone.utc)).total_seconds(), 1))
    except (TypeError, ValueError, OverflowError):
        return None


def read_rows(filename, sql, params=(), limit=MAX_ROWS):
    """Only constant file names and constant SELECT queries from this module."""
    path = ROOT / filename
    if not path.is_file():
        return {"available": False, "rows": [], "reason": "Local database is absent"}
    if not sql.lstrip().upper().startswith("SELECT ") or ";" in sql:
        raise ValueError("Read-only SELECT queries only")
    try:
        uri = path.resolve().as_uri() + "?mode=ro"
        with closing(sqlite3.connect(uri, uri=True, timeout=2)) as db:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA query_only=ON")
            rows = db.execute(sql, (*params, max(1, min(int(limit), MAX_ROWS)))).fetchall()
        return {"available": True, "rows": [dict(x) for x in rows]}
    except (sqlite3.Error, OSError, ValueError):
        return {"available": False, "rows": [], "reason": "Database unavailable or schema mismatch"}


def read_local_json(filename):
    path = ROOT / filename
    try:
        if not path.is_file() or path.stat().st_size > 16_384:
            return {}
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, UnicodeError, ValueError):
        return {}


def check_get(port, path, expected=(200,)):
    """GET only, loopback only, finite response and timeout; no caller URLs."""
    req = Request(
        f"http://127.0.0.1:{port}{path}",
        headers={"Accept": "application/json", "User-Agent": "ARGUS-ReadOnly-MCP/1.0"},
        method="GET",
    )
    try:
        with urlopen(req, timeout=3) as response:
            raw = response.read(MAX_HTTP_BYTES + 1)
            if len(raw) > MAX_HTTP_BYTES:
                return {"ok": False, "reason": "Response too large"}
            return {"ok": response.status in expected, "http_status": response.status,
                    "raw": raw}
    except HTTPError as exc:
        return {"ok": exc.code in expected, "http_status": exc.code}
    except (URLError, TimeoutError, OSError, ValueError):
        return {"ok": False, "reason": "Local endpoint unavailable"}


def get_json(port, path):
    status = check_get(port, path)
    if not status["ok"]:
        return {"available": False, "reason": status.get("reason", f"HTTP {status.get('http_status')}")}
    try:
        value = json.loads(status.get("raw", b"").decode("utf-8"))
        return {"available": True, "value": value}
    except (ValueError, UnicodeError):
        return {"available": False, "reason": "Unexpected local response"}


def health():
    """Summarize local ARGUS services and actual worker heartbeat; never arm the bot."""
    row = read_rows(WORKER_DB,
                    "SELECT name, value FROM cfg WHERE name IN "
                    "('armed','armed_at','paper_account_id','last_heartbeat') LIMIT ?",
                    limit=8)
    cfg = {x["name"]: x["value"] for x in row["rows"]}
    kill = (ROOT / KILL_FILE).is_file()
    supervisor = read_local_json(SUPERVISOR_FILE)
    supervisor_age = age_seconds(supervisor.get("updated_at"))
    heartbeat_age = age_seconds(cfg.get("last_heartbeat"))
    sup_fresh = supervisor_age is not None and supervisor_age <= FRESHNESS_SECONDS
    hb_fresh = heartbeat_age is not None and heartbeat_age <= FRESHNESS_SECONDS
    effective_armed = cfg.get("armed") == "true" and not kill
    sup_mode = str(supervisor.get("mode", "UNKNOWN"))
    worker_pid = supervisor.get("worker_pid") if sup_fresh and sup_mode == "RUNNING" else None
    api = {
        "signal_receiver_8001": check_get(8001, "/health")["ok"],
        "tradingview_gateway_8002": check_get(8002, "/tv-webhook", expected=(405,))["ok"],
        "argus_dashboard_8501": check_get(8501, "/health")["ok"],
    }
    return {
        "checked_at_utc": iso_now(),
        "status": "HEALTHY" if (all(api.values()) and effective_armed and
                               cfg.get("paper_account_id") and hb_fresh and sup_fresh and worker_pid)
                  else "REVIEW_REQUIRED",
        "local_http": api,
        "paper": {
            "configured_armed": cfg.get("armed") == "true",
            "effective_armed": effective_armed,
            "kill_switch": kill,
            "account_bound": bool(cfg.get("paper_account_id")),
            "worker_heartbeat_utc": cfg.get("last_heartbeat"),
            "worker_heartbeat_age_seconds": heartbeat_age,
            "worker_heartbeat_fresh": hb_fresh,
            "supervisor_mode": sup_mode if sup_fresh else "OFFLINE_OR_STALE",
            "supervisor_status_age_seconds": supervisor_age,
            "supervisor_status_fresh": sup_fresh,
            "worker_pid_reported": worker_pid,
        },
        "notes": [
            "Read-only local observations; a fresh heartbeat does not prove an order filled.",
            "No live broker credentials or direct broker requests are used.",
            "Supervisor PID is reported by supervisor, not independently OS-verified.",
        ],
    }


def signal_log(limit: int = 15):
    """Received signal actions and outcomes (no raw payloads or webhook secrets)."""
    res = read_rows(SIGNAL_DB,
                    "SELECT received_at, strategy, symbol, signal, result, intent_state "
                    "FROM events ORDER BY received_at DESC LIMIT ?",
                    limit=limit)
    return {"checked_at_utc": iso_now(), "available": res["available"],
            "events": res["rows"], "reason": res.get("reason")}


def worker_decisions(limit: int = 15):
    """Recorded worker outcomes without notes, broker IDs or secret-bearing details."""
    res = read_rows(WORKER_DB,
                    "SELECT received_at, action, outcome FROM signals "
                    "ORDER BY received_at DESC LIMIT ?",
                    limit=limit)
    for event in res["rows"]:
        # Outcomes are normally fixed machine strings, but bound their size as defense-in-depth.
        event["outcome"] = str(event["outcome"])[:160]
    return {"checked_at_utc": iso_now(), "available": res["available"],
            "decisions": res["rows"], "reason": res.get("reason")}


def paper_orders(limit: int = 15):
    """Existing local broker-order audit data; never send or modify orders."""
    res = read_rows(WORKER_DB,
                    "SELECT submitted_at, action, option_symbol, qty, state, "
                    "filled_qty, fill_price, filled_at FROM paper_orders "
                    "ORDER BY submitted_at DESC LIMIT ?",
                    limit=limit)
    return {"checked_at_utc": iso_now(), "available": res["available"],
            "orders": res["rows"], "reason": res.get("reason"),
            "note": "Local journal state; verify pending or unknown submissions at Alpaca PAPER."}


def fills(limit: int = 25):
    """Broker-confirmed local fill journal, newest first; no account/order identifiers."""
    res = read_rows(JOURNAL_DB,
                    "SELECT filled_at, option_symbol, mode, action, contracts, "
                    "fill_price, fees_usd FROM option_fills "
                    "ORDER BY filled_at DESC LIMIT ?",
                    limit=limit)
    rows = res["rows"]
    return {"checked_at_utc": iso_now(), "available": res["available"],
            "fills": rows, "reason": res.get("reason"),
            "note": "List limited; do not infer lifetime P&L from a truncated journal."}


def backtests(limit: int = 10):
    """A bounded view of stored backtest metadata; never run simulations."""
    response = get_json(8501, "/lab/api/backtests")
    if not response["available"]:
        return {"available": False, "reason": response["reason"], "runs": []}
    data = response["value"]
    if not isinstance(data, list):
        return {"available": False, "reason": "Unexpected run-list format", "runs": []}
    result = []
    for item in data[:max(1, min(int(limit), 20))]:
        if not isinstance(item, dict):
            continue
        keys = ("id", "label", "algorithm", "symbol", "timeframe",
                "source", "created_at", "trades", "total_pnl", "win_rate")
        result.append({key: item[key] for key in keys if key in item and
                       isinstance(item[key], (str, int, float, bool, type(None)))})
    return {"available": True, "runs": result, "checked_at_utc": iso_now()}


def research(symbol: str = "SPY"):
    """Current read-only watchlist metrics and headline sentiment; no trade signals."""
    symbol = symbol.strip().upper()
    if not ALLOWED_SYMBOL.fullmatch(symbol):
        return {"available": False, "reason": "Invalid ticker symbol"}
    # Avoid arbitrary URL/query injection: symbols are tightly validated.
    s = get_json(8501, f"/api/research/screener?tickers={symbol}")
    n = get_json(8501, f"/api/research/sentiment?symbol={symbol}")
    screener = None
    if s["available"] and isinstance(s["value"], dict):
        vals = s["value"].get("rows", [])
        if isinstance(vals, list) and vals and isinstance(vals[0], dict):
            permitted = ("symbol", "last", "date", "change_pct", "rsi_14", "ema20",
                         "ema50", "rvol", "realized_vol_20d_pct", "trend")
            screener = {k: vals[0][k] for k in permitted if k in vals[0]}
    sentiment = None
    if n["available"] and isinstance(n["value"], dict):
        v = n["value"]
        sentiment = {k: v.get(k) for k in ("average_compound", "label", "headline_count", "method")}
        sentiment["headline_titles_untrusted"] = [
            str(x.get("title", ""))[:170] for x in v.get("headlines", [])[:5]
            if isinstance(x, dict)
        ]
    return {"checked_at_utc": iso_now(), "symbol": symbol,
            "screener": screener, "sentiment": sentiment,
            "source": "ARGUS read-only Yahoo daily data / VADER news research; not FinBERT or execution data",
            "errors": [key for key, val in (("screener", s), ("sentiment", n)) if not val["available"]]}


def diagnostics():
    """Return all small core checks with no credentials, PII or raw log text."""
    return {"health": health(), "signals": signal_log(6),
            "worker_decisions": worker_decisions(6), "paper_orders": paper_orders(6),
            "fills": fills(8)}
