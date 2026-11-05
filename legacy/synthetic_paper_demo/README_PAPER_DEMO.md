# AION KAIROS V5 — Synthetic paper execution test

This adds a demonstration-only pipeline: **fake TradingView-format KAIROS V5 alert → original gateway validation → simulation server → synthetic PAPER fill in your existing execution journal → mobile AION dashboard**.

It does **not** fetch option quotes, use real prices, calculate live-option P&L, connect to SnapTrade, submit orders, or trade your Webull accounts.

The demonstration fixes a *fictitious* underlying SPY price of $700.00, a *fictitious* option entry ask of $1.25 and a *fictitious* exit bid of $1.10, one contract and $0.65 fee per side. Expected synthetic P&L: **-$16.30**. These values are not sourced from the market and must not be treated as strategy performance.

## Install on Windows

1. Extract the ZIP directly into `C:\Users\sathw\AION-Execution`. It includes three new files: `aion_paper_broker.py`, `aion_paper_cycle_test.py`, `install_aion_paper_demo.py`.
2. Open PowerShell and run:
   ```powershell
   cd C:\Users\sathw\AION-Execution
   .\.venv\Scripts\python.exe .\install_aion_paper_demo.py
   .\.venv\Scripts\python.exe .\aion_paper_cycle_test.py
   ```
3. Your existing AION dashboard on port 8501 should show **two PAPER fills** under Options Executions after refresh. If you installed the optional UI Source column, it explicitly shows `SYNTHETIC_PAPER_DEMO_NO_MARKET_QUOTES`.
4. Restart the existing simulation gateway (8002), signal simulator (8001), and dashboard (8501) *only when convenient* to load the patched web service code. Do not launch duplicate port processes. The local demo script itself runs the patched code in-process and can run before any restart.

## How the test is isolated

- The fake test goes through the **actual** gateway and server's FastAPI routes **in the test process**, with HTTP forwarding replaced by a local in-memory transport. It never sends traffic to Cloudflare or SnapTrade.
- Demo order IDs are explicitly tagged `AION_DEMO_<random12>_ENTRY/EXIT` and require the existing gateway's webhook secret. The secret stays on your PC and is not printed.
- The demo signal path is distinct from ordinary TradingView signals. Demo events **do not modify** the KAIROS V5 regular signal-state or events tables and can run outside market hours without changing normal session enforcement.
- The demo records actual synthetic **PAPER journal entries**, which is why it appears in the live dashboard. It never labels them as broker fills. Demo event IDs and filled pairs are idempotent.
- If the dashboard is also showing genuine or older PAPER fills, the paper P&L summary combines them; the test prints the P&L for its *own* run.

## What comes after the demo

To support real alerts with realistic paper fills, acquire a properly licensed options chain/quotes feed, define 0DTE strike/expiry/premium selection rules and a market-aware fill model. The `aion_paper_broker.py` deliberately handles *only synthetic test events*; ordinary live TradingView alerts continue logging signal intent with no paper fill inferred. Live orders additionally require broker-preview support, account permissions, appropriate automation authorization, risk controls, order status reconciliation and a separate approved execution adapter.

**Do not change `AION_MODE=SIMULATION` to LIVE.** The existing gateway/simulator refuse live mode by design.
