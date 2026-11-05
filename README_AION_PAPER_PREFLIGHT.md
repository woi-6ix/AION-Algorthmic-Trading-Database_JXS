# AION Alpaca Paper: chart + signal + account preflight

**Date:** November 5, 2026  
**Execution:** READ-ONLY. No orders. No POST endpoints. Doesn't arm, disarm, edit `.env`, update AION trading files or databases, or change positions.

This add-on diagnoses the previously observed `Alpaca DATA HTTP 400 on /v1beta1/options/bars` and checks signal delivery before any supervised broker-hosted KAIROS V5 paper trade. The existing $2,500 total-premium paper worker, live order logic, charts, gateway and dashboard are unchanged.

## Install (Windows PowerShell)

1. Keep your KAIROS engine (8001), gateway (8002), dashboard (8501), and Cloudflare tunnel running. Leave the Alpaca Paper AutoTrader **disarmed**.
2. Download `AION_Alpaca_Options_Signal_Preflight.zip` to Downloads.
3. Extract from inside `C:\Users\sathw\AION-Execution`:

```powershell
cd C:\Users\sathw\AION-Execution
Expand-Archive -Path "$env:USERPROFILE\Downloads\AION_Alpaca_Options_Signal_Preflight.zip" -DestinationPath . -Force
.\.venv\Scripts\python.exe .\aion_option_preflight.py --symbol SPY261105C00736000
```

The example symbol is the November 5, 2026 $736 SPY call from your previous Alpaca chain test. Use an actual OCC-formatted SPY option for subsequent sessions. The diagnostic does **not** force an order or create an alert.

## Checks performed

- Local service health via GET only: `/health` on 8001 and 8501, a GET on `/tv-webhook` (405 expected), and `/events` on 8001.
- Reads existing local paper worker armed/kill-switch status (does not modify it).
- Alpaca **Paper** account details, existing positions and pending orders via GET; no real broker API host used for account operations.
- Up to four read-only requests to `https://data.alpaca.markets/v1beta1/options/bars`, stopping after the first successful response with historical bars:
  1. `3Min`, `feed=indicative`, no end time.
  2. `3Min`, `feed=indicative`, explicitly capped at least 20 minutes before current time.
  3. `3Min`, `feed=opra`, with explicitly delayed end. Alpaca Basic may grant OPRA history that is more than 15 minutes old; this is not real-time OPRA access.
  4. `1Min`, `feed=opra`, same delayed end, to isolate timeframe-specific issues.

A response may have `HTTP 200` but zero bars for an illiquid contract. The script prints only the status and a shortened, redacted Alpaca error message; **never post your `.env` or API keys**. API rate limits and historical entitlements still apply.

## Interpreting the results

- If at least one `PASS ... bars` appears, the historical market data endpoint works; if the AION chart remains blank, a chart request parameter or UI path may need an update.
- If all attempts return `HTTP 400`, copy only the sanitized lines and statuses to ChatGPT so the correct parameter fix can be made.
- If `HTTP 401` / `403`, diagnose credentials or subscription permissions; no live trading should be enabled.
- If KAIROS events show `BUY_CALL`, `BUY_PUT`, `EXIT` with accepted results, the gateway and receiver have recorded relevant signals; **this is not proof of Alpaca order execution**.
- If there are unexpected Alpaca Paper positions or open orders, reconcile those manually and keep the bot disarmed.

## Next paper trade (not triggered by this tool)

The KAIROS Paper AutoTrader uses up to $2,500 premium per entry, paper-only, with 09:35–14:30 ET entry restriction and a 14:45 ET safety exit attempt. It must be explicitly rearmed by the owner during a subsequent market session, and the first entry/exit should be watched directly in Alpaca Paper. The options pricing feed is indicative; simulated fills and performance aren't equivalent to live 0DTE execution. Do not replay old alerts or bypass the emergency cutoff to force a paper trade.

## Unit tests

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests\test_option_preflight.py
```

Tests use mock market-data responses and do not contact Alpaca or TradingView.