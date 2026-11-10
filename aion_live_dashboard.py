"""AION Quant Labs private read-only web dashboard (no trading capabilities).

Runs separately from the user's AION Quant Terminal (port 8000), KAIROS V5
simulator (8001), and TradingView webhook gateway (8002).
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse

from aion_execution_journal import DEFAULT_DB, read_fills, summarize_fills
from aion_research import router as research_router

ROOT = Path(__file__).resolve().parent
# The user-supplied environment is NOT read for API credentials. This service
# only calls the local, read-only AION /health, /status, /events endpoints.
SOURCE = "http://127.0.0.1:8001"

app = FastAPI(title="ARGUS Terminal | AION Quant Labs — Read Only", docs_url=None, redoc_url=None, openapi_url=None)
app.include_router(research_router)

# AION original Quant Terminal v0.2 research engine (same process, /lab prefix).
# It is intentionally isolated from the live order execution adapter.
from aion_backtest_engine.server import app as aion_embedded_backtest_app

# AION_ALPACA_PAPER_ALGO_PANEL_V1 — read-only chart/status only, no trade endpoints.
from aion_alpaca_paper_panel import app as aion_alpaca_paper_panel
app.mount("/alpaca-paper", aion_alpaca_paper_panel, name="aion_alpaca_paper_panel")

app.mount("/lab", aion_embedded_backtest_app, name="aion_integrated_backtests")



def fetch_endpoint(path: str) -> dict:
    request = Request(f"{SOURCE}{path}", headers={"Accept": "application/json", "User-Agent": "AION-Live-Monitor/1.0"})
    with urlopen(request, timeout=1.8) as response:
        value = json.loads(response.read(200_000).decode("utf-8"))
        if not isinstance(value, dict):
            raise ValueError("Unexpected AION response")
        return value


@app.get("/static/aion-logo.png", include_in_schema=False)
def aion_brand_logo():
    """Serve only the official logo PNG (never expose other local static files)."""
    return FileResponse(
        ROOT / "static" / "aion-logo.png",
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=86400", "X-Content-Type-Options": "nosniff"},
    )


@app.get("/")
def homepage():
    return FileResponse(ROOT / "static" / "index.html", media_type="text/html", headers={"Cache-Control": "no-store"})


@app.get("/api/snapshot")
def snapshot():
    errors = []
    health = {}
    status = {}
    events = []
    for label, path in (("health", "/health"), ("status", "/status"), ("events", "/events?limit=50")):
        try:
            result = fetch_endpoint(path)
            if label == "health":
                health = result
            elif label == "status":
                status = result
            else:
                events = result.get("events", [])
                if not isinstance(events, list):
                    raise ValueError("Unexpected events type")
        except (HTTPError, URLError, TimeoutError, ValueError, OSError, json.JSONDecodeError) as exc:
            errors.append(f"AION {label} unavailable ({type(exc).__name__})")
    try:
        fills = read_fills(DEFAULT_DB, limit=500)
        fills_summary = summarize_fills(fills)
    except Exception:
        fills = []
        fills_summary = {"fills_recorded": 0, "realized_pnl_usd": {"PAPER": 0.0, "LIVE": 0.0},
                         "open_contracts": {"PAPER": 0, "LIVE": 0}, "pnl_may_be_incomplete": True}
        errors.append("Execution journal unavailable")
    body = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "aion_online": not errors or all("Execution journal" in s for s in errors),
        "health": {k: health.get(k) for k in ("service", "strategy", "mode", "broker_orders_enabled", "session_enforced")},
        "status": {k: status.get(k) for k in ("strategy", "intent_state", "signals_logged")},
        "events": events,
        "fills": fills[:100],
        "fill_summary": fills_summary,
        "errors": errors,
        "notice": "Signals are not executions. Fills appear only when a future paper/live adapter records confirmed fills.",
    }
    return JSONResponse(body, headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})


@app.get("/health")
def monitor_health():
    return {"service": "AION-Live-Monitor", "read_only": True, "execution_enabled": False}
