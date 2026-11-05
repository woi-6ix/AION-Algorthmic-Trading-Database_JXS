# AION Alpaca Paper options chart — HTTP 400 fix

When an options contract expires **today**, the original chart downloader requested historical bars from 09:30 to **16:00 ET**, even during the trading session. Alpaca's Basic plan can reject a request that includes recent/inaccessible bars. The updated functions **omit `end`** for today's option, allowing Alpaca to choose the most recent permitted history. Expired contracts still use their correct historical session bounds.

Alpaca documents historical options bar data limits and that omitting `end` lets the API apply the account's entitlement cutoff. This fix does **not** remove the 15-minute delay, add an OPRA subscription, or change how the paper trading bot obtains quotes.

**Safety:** Does not read or modify `.env`, broker authentication, account binding, armed state, API order methods, SQLite databases, alerts, risk settings, or signal ingestion. Backs up both changed Python files. The fixes are only in GET market-data requests and chart display.

## Install

1. If the Alpaca paper-order worker is running, disarm it first and stop it with `Ctrl+C` before applying code changes. Disarming doesn't cancel open paper orders or exit open positions; inspect Alpaca Paper separately.
2. Extract `install_aion_alpaca_chart_fix.py` into `C:\Users\sathw\AION-Execution`.
3. Run:

```powershell
cd C:\Users\sathw\AION-Execution
.\.venv\Scripts\python.exe .\install_aion_alpaca_chart_fix.py
.\.venv\Scripts\python.exe .\aion_alpaca_paper_algo.py chart --symbol SPY261105C00736000
```

The symbol expires November 5, 2026. Future use: replace it with a currently or previously listed SPY contract. Same-day historical bars may be 15 minutes behind, and a valid contract can have zero bars.

4. Restart the dashboard port `8501` using the usual command:

```powershell
.\.venv\Scripts\python.exe -m uvicorn aion_live_dashboard:app --host 127.0.0.1 --port 8501
```

5. Open `/alpaca-paper/` in the AION dashboard and select the same contract's **View** button.

If HTTP 400 persists, please report only the HTTP status and request time; do not post API credentials. A different data subscription/entitlement failure may be involved.
