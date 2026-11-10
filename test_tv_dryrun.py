"""SAFE fake TradingView alert test for ARGUS (no forwarding and no broker orders).

Requires the updated gateway running on localhost:8002. The /tv-webhook-test
endpoint validates the real alert schema, timestamp, token and signal mapping,
but NEVER writes to signal SQLite or sends broker orders. An old gateway
returns HTTP 404 rather than interpreting this as a trading alert.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")
secret = os.getenv("AION_TV_WEBHOOK_SECRET", "").strip()
if len(secret) < 32:
    raise SystemExit("Missing/short AION_TV_WEBHOOK_SECRET in local .env")

url = "http://127.0.0.1:8002/tv-webhook-test"  # NEVER use /tv-webhook here
data = {
    "webhook_secret": secret,
    "strategy": "KAIROS_V5",
    "ticker": "BITSTAMP:BTCUSD",
    "timeframe": "3",
    "order_id": "ARGUS-TEST-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ"),
    "order_action": "buy",
    "order_message": "KAIROS LC V5 LONG | BTC AUTHENTICATED DRY RUN",
    "timenow": datetime.now(timezone.utc).isoformat(),
}
req = urllib.request.Request(
    url,
    data=json.dumps(data).encode("utf-8"),
    headers={"Content-Type": "application/json"},
    method="POST",
)
try:
    with urllib.request.urlopen(req, timeout=5) as resp:
        result = json.load(resp)
except urllib.error.HTTPError as err:
    if err.code == 404:
        raise SystemExit("Test endpoint missing: update/restart port 8002 gateway first") from None
    raise SystemExit(f"Gateway dry-run returned HTTP {err.code}. Check secret, payload and server logs.") from None
except (urllib.error.URLError, TimeoutError) as err:
    raise SystemExit("Gateway not reachable on localhost:8002. Check the AION TradingView gateway task.") from err

if not (result.get("result") == "TEST_VALIDATED_NO_FORWARD"
        and result.get("translated_signal") == "BUY_CALL"
        and result.get("intent_state") == "UNCHANGED"
        and result.get("test_only") is True
        and result.get("broker_order_sent") is False
        and result.get("validated_ticker") == "BITSTAMP:BTCUSD"):
    raise SystemExit("Unexpected response; do not assume the test was safe: " + repr(result))

print("PASS: TradingView-shaped BITSTAMP:BTCUSD 3-minute KAIROS V5 LONG authenticated and translated for TEST ONLY")
print("PASS: no signal forwarded, no queued event, no PAPER/live order sent")
print("RESULT:", json.dumps({k: v for k, v in result.items() if k != "webhook_secret"}, sort_keys=True))
