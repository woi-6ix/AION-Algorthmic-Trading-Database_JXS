# AION Architecture

```
TradingView KAIROS V5 3m (alerts)
      |
      | HTTPS POST, shared-secret payload, URL currently via Cloudflare tunnel
      v
AION TV gateway :8002 (local)
      | validates and forwards accepted alerts
      v
AION KAIROS V5 signal simulator :8001 (local)
      | accepted events -> SQLite
      v
AION Alpaca Paper AutoTrader CLI (separate supervised worker)
      | options snapshot / paper account checks / limits
      v
Alpaca Paper API (hard-coded paper endpoint)
      | broker order state / fills
      v
AION execution journal (SQLite)
      |                   +-> AION options chart read-only panel
      +-> AION Dashboard :8501 /lab/ /alpaca-paper/
                         ^
                         |
                Tailscale Serve -> phone
```

**Independent chart subsystem:** AION `/lab/` hosts the earlier v0.2 stock and historical options backtesting engine. That replay cache is not the same as Alpaca's current indicative options chain, and no claim of Pine KAIROS V5 parity is made.

**Order safety:** Differentiate signal receipt, order submitted, order accepted, partially filled, fully filled, order canceled, rejected and exit. No order submission occurs through the browser UI. See `README_AION_ALPACA_PAPER_AUTOTRADE.md` for worker details.
