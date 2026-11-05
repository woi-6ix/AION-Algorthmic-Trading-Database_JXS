# AION — Alpaca Paper trading connection through SnapTrade

This helper **only opens** a SnapTrade Connection Portal. It makes **no orders** and does not change the AION executor.

## Requirements
- Your existing `C:\Users\sathw\AION-Execution\.env` contains `SNAPTRADE_CLIENT_ID`, `SNAPTRADE_CONSUMER_KEY`, and `AION_MODE=SIMULATION`.
- `snaptrade-python-sdk` and `python-dotenv` installed inside `.venv`.
- A free **Alpaca Paper Only** account. Sign up directly at https://app.alpaca.markets if you don't have one. Do not create or fund a live brokerage account just to paper trade.

## Install
Extract `connect_alpaca_paper.py` to `C:\Users\sathw\AION-Execution` (do not extract over `.env`).

```powershell
cd C:\Users\sathw\AION-Execution
.\.venv\Scripts\python.exe -m pip install snaptrade-python-sdk python-dotenv
.\.venv\Scripts\python.exe .\connect_alpaca_paper.py
```

The helper requests **broker=`ALPACA-PAPER`, connection_type=`trade`** using your local Personal API credentials; it refuses to open any link configured for read-only or a different brokerage. Complete account authorization in the opened browser. URLs expire in five minutes; rerun script to regenerate.

## After the connection
1. Tell ChatGPT you have connected Alpaca Paper. It can inspect the SnapTrade account/connection listing (read-only) without sending orders.
2. Verify paper account options permissions and whether SnapTrade supports *option order routing* for Alpaca Paper. Alpaca itself supports paper options, but SnapTrade's brokerage-specific options support needs confirmation.
3. Only after that, connect AION's KAIROS V5 paper order worker; use an explicit paper account ID allowlist. Webull CASH and MARGIN remain excluded.
4. Test order impact and 1-contract simulated SPY option order while regular markets are open. Confirm with the user before any actual order submission, even to paper.

Safety: DO NOT send `.env`, SnapTrade API keys, paper brokerage API keys, or the one-time portal URL to ChatGPT. This helper does not modify the existing gateway, journal, simulator, or trading-mode flags.
