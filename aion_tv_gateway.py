"""AION KAIROS V5 public-facing *simulation-only* TradingView ingress.

Expose only THIS service (default 127.0.0.1:8002) through an HTTPS tunnel.
Keep aion_kairos_server.py (port 8001) on loopback; never tunnel it directly.
Dedicated webhook secret is NOT the SnapTrade API credentials.

No brokerage imports. No execution code. No reverse-proxy of arbitrary paths.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / '.env')

if os.getenv('AION_MODE', 'SIMULATION').upper() != 'SIMULATION':
    raise RuntimeError('Gateway is SIMULATION ONLY. Set AION_MODE=SIMULATION.')

WEBHOOK_SECRET = os.environ.get('AION_TV_WEBHOOK_SECRET', '').strip()
if len(WEBHOOK_SECRET) < 32:
    raise RuntimeError('Set AION_TV_WEBHOOK_SECRET (at least 32 chars) using configure_tv_secret.py')

AION_ORIGIN = 'http://127.0.0.1:8001/webhook'
MAX_BODY_BYTES = 8192
app = FastAPI(
    title='AION KAIROS V5 - TradingView Simulation Gateway',
    docs_url=None, redoc_url=None, openapi_url=None,
)

class TVOrderFill(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    webhook_secret: str = Field(min_length=1, max_length=256)
    strategy: str = Field(min_length=1, max_length=80)
    ticker: str = Field(min_length=1, max_length=25)
    timeframe: str = Field(min_length=1, max_length=8)
    order_id: str = Field(min_length=1, max_length=160)
    order_action: str = Field(min_length=1, max_length=16)
    order_message: str = Field(min_length=1, max_length=500)
    timenow: str = Field(min_length=1, max_length=45)

# Only accept messages embedded by the actual KAIROS V5 Pine file.
# Do not infer action from 'buy' or 'sell': those are UNDERLYING share orders.
# This translation is based on explicit strategy alert_message prefixes.
ORDER_PREFIXES = (
    ('KAIROS LC V5 CLOSE LONG - KERNEL FLIP', 'EXIT'),
    ('KAIROS LC V5 CLOSE SHORT - KERNEL FLIP', 'EXIT'),
    ('KAIROS LC V5 SESSION CUTOFF', 'EXIT'),
    ('KAIROS LC V5 LONG EXIT', 'EXIT'),
    ('KAIROS LC V5 SHORT EXIT', 'EXIT'),
    ('KAIROS LC V5 LONG', 'BUY_CALL'),
    ('KAIROS LC V5 SHORT', 'BUY_PUT'),
)

def translate_message(message: str) -> str:
    normalized = message.strip().upper()
    for prefix, action in ORDER_PREFIXES:
        if normalized == prefix or normalized.startswith(prefix + ' |'):
            return action
    raise HTTPException(422, 'Unrecognized KAIROS V5 order-fill message')


def get_emitted_at(raw: str) -> datetime:
    try:
        d = datetime.fromisoformat(raw.replace('Z', '+00:00'))
    except ValueError:
        raise HTTPException(422, 'Invalid TradingView timenow timestamp') from None
    if d.tzinfo is None:
        raise HTTPException(422, 'TradingView timestamp must include timezone')
    lag = (datetime.now(timezone.utc) - d.astimezone(timezone.utc)).total_seconds()
    if lag < -15 or lag > 180:
        raise HTTPException(422, 'Alert timestamp is stale or future-dated')
    return d


@app.post('/tv-webhook-test')
async def tradingview_dryrun(request: Request):
    """Authenticated local test path: validates a TV fill; NEVER forwards or logs it."""
    if request.headers.get('content-type', '').split(';')[0].strip().lower() != 'application/json':
        raise HTTPException(415, 'JSON required')
    raw = await request.body()
    if len(raw) > MAX_BODY_BYTES:
        raise HTTPException(413, 'Payload too large')
    try:
        obj = TVOrderFill.model_validate_json(raw)
    except Exception:
        raise HTTPException(422, 'Invalid TradingView order-fill JSON') from None
    if not hmac.compare_digest(obj.webhook_secret, WEBHOOK_SECRET):
        raise HTTPException(403, 'Forbidden')
    if obj.strategy != 'KAIROS_V5' or obj.ticker.strip().upper() not in ('SPY', 'AMEX:SPY'):
        raise HTTPException(422, 'Only KAIROS_V5 SPY is accepted')
    if obj.timeframe.strip().lower() not in ('3', '3m'):
        raise HTTPException(422, 'Only 3-minute strategy fills are accepted')
    if obj.order_action.lower() not in ('buy', 'sell'):
        raise HTTPException(422, 'Unknown underlying strategy order action')
    get_emitted_at(obj.timenow)
    signal = translate_message(obj.order_message)
    return {
        'result': 'TEST_VALIDATED_NO_FORWARD',
        'translated_signal': signal,
        'intent_state': 'UNCHANGED',
        'mode': 'SIMULATION',
        'broker_order_sent': False,
        'test_only': True,
    }


@app.post('/tv-webhook')
async def tradingview_webhook(request: Request):
    if request.headers.get('content-type', '').split(';')[0].strip().lower() != 'application/json':
        raise HTTPException(415, 'JSON required')
    raw = await request.body()
    if len(raw) > MAX_BODY_BYTES:
        raise HTTPException(413, 'Payload too large')
    try:
        obj = TVOrderFill.model_validate_json(raw)
    except Exception:
        raise HTTPException(422, 'Invalid TradingView order-fill JSON') from None

    if not hmac.compare_digest(obj.webhook_secret, WEBHOOK_SECRET):
        raise HTTPException(403, 'Forbidden')
    if obj.strategy != 'KAIROS_V5':
        raise HTTPException(422, 'Only KAIROS_V5 is accepted')
    if obj.ticker.strip().upper() not in ('SPY', 'AMEX:SPY'):
        raise HTTPException(422, 'Only SPY is accepted')
    if obj.timeframe.strip().lower() not in ('3', '3m'):
        raise HTTPException(422, 'Only 3-minute strategy fills are accepted')
    if obj.order_action.lower() not in ('buy', 'sell'):
        raise HTTPException(422, 'Unknown underlying strategy order action')
    d = get_emitted_at(obj.timenow)
    signal = translate_message(obj.order_message)
    unique_source = '|'.join((obj.strategy, obj.ticker, obj.timeframe, obj.order_id,
                              obj.order_action, obj.order_message, obj.timenow))
    event_id = 'tvv5-' + hashlib.sha256(unique_source.encode('utf-8')).hexdigest()
    upstream_json = json.dumps({
        'event_id': event_id,
        'strategy': 'KAIROS_V5',
        'symbol': 'SPY',
        'timeframe': '3m',
        'signal': signal,
        'emitted_at': d.astimezone(timezone.utc).isoformat(),
    }).encode('utf-8')

    # The existing simulator remains the ONLY writer of its signal/state DB.
    req = urllib.request.Request(
        AION_ORIGIN,
        data=upstream_json,
        headers={'Content-Type': 'application/json'},
        method='POST',
    )
    try:
        with urllib.request.urlopen(req, timeout=1.7) as resp:
            upstream = json.load(resp)
    except urllib.error.HTTPError as e:
        # No secrets, raw payloads, or brokerage credentials in responses.
        raise HTTPException(422 if 400 <= e.code < 500 else 503,
                            'AION signal receiver declined or failed to process event') from None
    except (urllib.error.URLError, TimeoutError):
        raise HTTPException(503, 'Local AION signal receiver unavailable') from None
    return {
        'result': upstream.get('result'),
        'intent_state': upstream.get('intent_state'),
        'mode': 'SIMULATION',
        'broker_order_sent': False,
    }
