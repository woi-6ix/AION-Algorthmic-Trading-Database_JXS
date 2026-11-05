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
      | paper-only, $2,500 total premium cap, contract sizing
      | KAIROS signal exits, sequential reversals, emergency 14:45 cutoff
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

## KAIROS V5 paper order states (Nov 2026)

The signal-only simulator handles `SIGNAL_LOGGED` (entry), `EXIT_INTENT_LOGGED` (exit), and `REVERSAL_INTENT_LOGGED` (opposite Pine `strategy.entry` without a separate close). The bot maps those events to Alpaca Paper `BUY_TO_OPEN` and `SELL_TO_CLOSE`, confirms and journals cumulative partial fills, and requires a broker-flat position before opening the reversed contract. It queues exits when entry cancellation is pending. All abnormal/stale/out-of-sync situations halt automation or require manual Alpaca Paper intervention. These are paper workflows, **not a guarantee** of unattended 0DTE liquidation.