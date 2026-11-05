"""AION SPY option chart and KAIROS V5 PAPER readiness preflight.

Strictly read-only. Does not arm a bot, write state, submit orders, or
send a webhook. No secrets or brokerage account IDs are printed.

Usage: python aion_option_preflight.py --symbol SPY261105C00736000
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from aion_alpaca_paper_algo import AlpacaPaper, KILL, NY, OCC, armed

DATA_HOST = "https://data.alpaca.markets"
BAR_ENDPOINT = "/v1beta1/options/bars"


def option_session(symbol: str, now: datetime | None = None) -> tuple[str, str | None]:
    """Build safe NYC 0DTE session query bounds; never ask for latest 15 min."""
    if not OCC.fullmatch(symbol):
        raise ValueError("Expected SPY OCC contract format, such as SPY261105C00736000")
    expiry = datetime.strptime(symbol[3:9], "%y%m%d").date()
    n = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if expiry > n.astimezone(NY).date():
        raise ValueError("Contract expires in the future; historical chart not yet available")
    start = datetime.combine(expiry, time(9, 30), NY).astimezone(timezone.utc)
    close = datetime.combine(expiry, time(16, 0), NY).astimezone(timezone.utc)
    latest_entitled = n - timedelta(minutes=20)  # conservatively outside free-plan 15-min restriction
    capped = min(close, latest_entitled)
    if capped <= start:
        raise ValueError("Historical options data not available until at least 20 minutes after opening")
    return start.isoformat(), capped.isoformat()


def clean_error_message(raw: bytes) -> str:
    """Only show a short, redacted Alpaca error message, never response headers."""
    try:
        decoded = json.loads(raw[:4096])
        message = decoded.get("message") if isinstance(decoded, dict) else None
    except (ValueError, UnicodeDecodeError):
        message = None
    if not isinstance(message, str):
        return "No safe API error message available"
    message = re.sub(r"https?://\S+", "[URL REDACTED]", message)
    message = re.sub(r"(?i)(?:AK|PK)[A-Z0-9]{10,}", "[TOKEN REDACTED]", message)
    message = re.sub(r"(?i)(?:secret|api[_ -]?key|token|authorization)\s*[:=]\s*\S+", "[CREDENTIAL REDACTED]", message)
    message = re.sub(r"\b[0-9a-fA-F]{25,}\b", "[ID REDACTED]", message)
    return " ".join(message[:180].split())


def local_get(url: str) -> tuple[int | None, dict | None]:
    """GET local-only status paths; never make POST requests."""
    try:
        with urllib.request.urlopen(url, timeout=3) as r:
            value = json.load(r)
            return r.status, value if isinstance(value, dict) else None
    except urllib.error.HTTPError as exc:
        return exc.code, None
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return None, None


def run_readonly_chart_probe(api: AlpacaPaper, symbol: str, now: datetime | None = None) -> bool:
    start, end = option_session(symbol, now)
    cases = [
        ("3Min indicative (subscription default end)", "3Min", "indicative", None),
        ("3Min indicative (delayed end)", "3Min", "indicative", end),
        ("3Min OPRA (delayed end)", "3Min", "opra", end),
        ("1Min OPRA (delayed end)", "1Min", "opra", end),
    ]
    for label, timeframe, feed, finish in cases:
        params = {"symbols": symbol, "timeframe": timeframe, "start": start,
                  "feed": feed, "sort": "asc", "limit": 1000}
        if finish is not None:
            params["end"] = finish
        # Hard-coded MARKET-DATA HTTPS URL; GET only; paper trading host never called.
        url = DATA_HOST + BAR_ENDPOINT + "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers=api.headers, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=8) as response:
                obj = json.load(response)
            bars = (obj.get("bars") or {}).get(symbol) or []
            if bars:
                print(f"  PASS {label}: {len(bars)} bars, last {bars[-1].get('t', 'unknown')}")
                print("  NOTE: Historical charts can lag by 15+ minutes; these are not order-entry quotes.")
                return True
            print(f"  EMPTY {label}: HTTP 200, no bars for this contract")
        except urllib.error.HTTPError as e:
            print(f"  HTTP {e.code} {label}: {clean_error_message(e.read(4096))}")
            if e.code in (401, 403, 429):
                print("  STOP: Authentication, entitlement, or rate limiting requires attention.")
                return False
        except (urllib.error.URLError, TimeoutError, ValueError, OSError) as e:
            print(f"  UNAVAILABLE {label}: {type(e).__name__}")
            return False
    print("  FAIL: none of the allowed read-only historical data requests returned bars.")
    return False


def main() -> int:
    p = argparse.ArgumentParser(description="Read-only AION Alpaca PAPER & options-chart preflight")
    p.add_argument("--symbol", required=True, help="SPY OCC option symbol (e.g. SPY261105C00736000)")
    args = p.parse_args()
    try:
        option_session(args.symbol)
    except ValueError as e:
        print("Invalid chart input:", e)
        return 2

    print("AION read-only preflight. No POST, no broker orders, no auto-arming.")
    for label, url in (
        ("KAIROS 8001", "http://127.0.0.1:8001/health"),
        ("Webhook 8002", "http://127.0.0.1:8002/tv-webhook"),
        ("Dashboard 8501", "http://127.0.0.1:8501/health"),
    ):
        code, data = local_get(url)
        ok = (code == 405 if "Webhook" in label else code == 200)
        print(f"  {'PASS' if ok else 'CHECK'} {label}: HTTP {code if code is not None else 'offline'}")
        if label.startswith("KAIROS") and data:
            print(f"    Mode={data.get('mode')} Broker orders enabled={data.get('broker_orders_enabled')}")

    status, event_data = local_get("http://127.0.0.1:8001/events?limit=8")
    if status == 200 and event_data:
        events = event_data.get("events") or []
        print(f"  KAIROS recent events: {len(events)}")
        for event in events[:5]:
            print("   ", event.get("received_at"), event.get("signal"), event.get("result"))
    else:
        print("  CHECK KAIROS events: local receiver unavailable")

    if armed():
        print("  WARNING: PAPER BOT IS ARMED. Disarm before further diagnostics or editing code.")
    else:
        print("  PASS Paper bot: DISARMED", "(kill switch active)" if KILL.exists() else "")
    try:
        api = AlpacaPaper()  # Uses only local paper keys and hard-coded endpoints.
        account = api.account()
        print("  Alpaca PAPER account:", account.get("status"), "options level", account.get("options_trading_level"))
        positions = api.positions()
        orders = api.orders()
        print(f"  Alpaca PAPER broker positions: {len(positions)}; open orders: {len(orders)}")
        if positions or orders:
            print("  CAUTION: Inspect positions/orders manually before arming. No orders changed.")
        print("  Checking historical options data:")
        ok = run_readonly_chart_probe(api, args.symbol)
        if ok:
            print("PRECHECK COMPLETE: Chart source responds. Continue verifying AION chart UI separately.")
        else:
            print("PRECHECK COMPLETE: Chart data needs attention; share status/message above, not keys.")
        return 0 if ok else 1
    except Exception as exc:
        # Never print raw HTTP response bodies or credentials.
        print("  Alpaca PAPER check unavailable:", type(exc).__name__)
        print("  If the normal 'check' command also fails, verify paper API key configuration locally.")
        return 1


if __name__ == "__main__":
    sys.exit(main())