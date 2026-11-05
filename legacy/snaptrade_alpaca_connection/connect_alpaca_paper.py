"""
AION / SnapTrade: request an Alpaca Paper TRADE connection.

- Uses existing SnapTrade Personal API credentials in local .env
- Requests broker ALPACA-PAPER and *strict* connection_type="trade"
- Does not access Webull accounts
- Does not place, preview, cancel, or modify orders
- Never prints local API credentials or full error payloads
"""
from __future__ import annotations

import os
import sys
import webbrowser
from pathlib import Path
from urllib.parse import urlparse, parse_qs

ROOT = Path(__file__).resolve().parent

def main() -> int:
    try:
        from dotenv import load_dotenv
        from snaptrade_client import SnapTrade, SnapTradeAuth
    except ImportError:
        print("Missing package(s). Install with:")
        print(r".\.venv\Scripts\python.exe -m pip install snaptrade-python-sdk python-dotenv")
        return 2

    load_dotenv(ROOT / ".env")
    mode = os.getenv("AION_MODE", "SIMULATION").strip().upper()
    if mode != "SIMULATION":
        print("For connection setup, keep AION_MODE=SIMULATION. No trading changes were made.")
        return 2

    cid = os.getenv("SNAPTRADE_CLIENT_ID", "").strip()
    key = os.getenv("SNAPTRADE_CONSUMER_KEY", "").strip()
    if not cid or not key:
        print("Missing SNAPTRADE_CLIENT_ID or SNAPTRADE_CONSUMER_KEY in local .env.")
        print("Do not share credentials in chat.")
        return 2

    print("Requesting Alpaca Paper trade-enabled connection (no orders)...")
    try:
        client = SnapTrade(auth=SnapTradeAuth.personal_api_key(client_id=cid, consumer_key=key))
        response = client.authentication.login_snap_trade_user(
            broker="ALPACA-PAPER",
            connection_type="trade",
        )
        body = getattr(response, "body", response)
        if not isinstance(body, dict):
            if hasattr(body, "model_dump"):
                body = body.model_dump()
            elif hasattr(body, "to_dict"):
                body = body.to_dict()
        if not isinstance(body, dict):
            print("SnapTrade returned an unexpected response. No connection opened.")
            return 3
        url = body.get("redirectURI") or body.get("redirect_uri")
        if not isinstance(url, str) or not url.startswith("https://app.snaptrade.com/"):
            print("SnapTrade did not return the expected secure connection portal.")
            return 3
        parsed = urlparse(url)
        params = parse_qs(parsed.query)
        brokers = params.get("broker", [])
        types = params.get("connectionType", [])
        if brokers != ["ALPACA-PAPER"] or types != ["trade"]:
            print("Safety check failed: portal is not explicitly ALPACA-PAPER / trade.")
            print("No portal opened. Contact SnapTrade support for connection permissions.")
            return 3
    except Exception as exc:
        # Do not print raw API error content; it can contain sensitive request data.
        print(f"SnapTrade connection request failed ({type(exc).__name__}).")
        print("Check whether trade-enabled Alpaca Paper access is allowed for your Personal API key.")
        return 3

    print("Verified portal broker: ALPACA-PAPER")
    print("Verified portal access requested: TRADE (not read-only)")
    print("Your portal expires in 5 minutes. Complete the sign-in yourself.")
    opened = webbrowser.open(url, new=2)
    if not opened:
        print("Could not automatically launch browser.")
        print("Copy the following one-time URL from THIS LOCAL terminal only. Do not paste it into chat:")
        print(url)
    else:
        print("Connection portal opened in your browser.")
    print("AFTER login: verify the new connection is Alpaca Paper before configuring AION.")
    print("No trades have been placed. Webull accounts have not been touched.")
    return 0

if __name__ == "__main__":
    sys.exit(main())
