# AION Quant Labs — Integrated Backtesting Upgrade

This installer merges the user's original AION Quant Terminal v0.2 research engine into the existing AION Mobile Trading & Signals website. **One web server and one Tailscale address:** `127.0.0.1:8501` on the PC / the existing `.ts.net` address on the iPhone.

The original backtesting code is bundled as `backtest_bundle/` and installed into `aion_backtest_engine/`. Its API is mounted under `/lab`, so stock-price ingestion, research backtests, quote-driven 0DTE replay and CSV imports work **directly inside the mobile site**. The complete original research UI is also embedded in an Advanced section at `/lab/`. No longer need the separate port-8000 website.

## Install (Windows PowerShell)

1. Download `AION_Unified_Trading_Research_Backtesting.zip` to your Downloads folder.
2. On Windows PowerShell:

```powershell
cd C:\Users\sathw\AION-Execution
Expand-Archive -Path "$env:USERPROFILE\Downloads\AION_Unified_Trading_Research_Backtesting.zip" -DestinationPath . -Force
.\.venv\Scripts\python.exe -m pip install -r .\requirements-aion-unified.txt
.\.venv\Scripts\python.exe .\install_aion_unified_backtesting.py
```

3. The installer updates **only** `aion_live_dashboard.py` and `static/index.html`; it backs them up first. Bundled original backtest code lives in its own subfolder. It will try to migrate `data/aion.sqlite3` from your old original Quant Terminal directory if the destination database does not already exist. The old database is never deleted.
4. Stop **only** the old dashboard process running on port 8501 with Ctrl+C and restart:

```powershell
.\.venv\Scripts\python.exe -m uvicorn aion_live_dashboard:app --host 127.0.0.1 --port 8501
```

5. Open `http://127.0.0.1:8501/` locally or your **existing private Tailscale Serve** URL on your iPhone. Select **Backtesting**. You do not need to change your Tailscale port mapping. Tailscale, AION server on 8001 and TradingView gateway 8002 remain as before. Stop port 8000 if you no longer need the old original standalone site.
6. Optional offline verification from another PowerShell window:

```powershell
cd C:\Users\sathw\AION-Execution
.\.venv\Scripts\python.exe .\check_aion_unified.py
```

For optional independent fitted HMM/GARCH models in the **advanced original research** view:

```powershell
.\.venv\Scripts\python.exe -m pip install -r .\requirements-aion-unified-advanced.txt
```

## What the Backtesting tab does

- **Stock data**: automatic recent Yahoo or entitled Massive OHLCV; cached or imported OHLCV; configurable ticker, timeframe and dates. Yahoo's deep intraday bars are limited and may be delayed. Data source and coverage are shown.
- **KAIROS core stock backtest**: runs the original Python KAIROS V5 core port on shares; configure notional, R target, ATR stop, kernel smoothing, Volatility Push, slope and moving average gates. **This does not implement LC/ML or walk-forward HMM entry filters, and does not reproduce exact Pine trades.**
- **0DTE option quote replay**: choose the core stock-run events or genuine imported Pine signal CSV; provide historical same-day option bid/ask quotes (CSV or entitled provider) to calculate long call/put returns. Actual missing quotes => skipped trades, not fabricated returns. Automatic ATM contract selection only for SPY/QQQ/IWM.
- **Saved backtests**: imports your TradingView Strategy Tester List-of-Trades CSV; shows recorded trades, win rate, realized trade P&L, profit factor, drawdown and cumulative equity.
- **Advanced original terminal**: the prior research interface works inside this site at `/lab/`, with data APIs under `/lab/api/...`. This is not a link to port 8000.

## Security and current execution status

- **No live options order submission has been added.** SnapTrade/Webull is not imported or invoked by this backtesting engine.
- AION V5 signals and existing fill journal are retained, untouched by installation. The live trading monitoring tab **still only shows confirmed fills that an execution adapter records**; price candles or signals do not create fake brokerage fills.
- The integrated research APIs permit uploading CSVs and running research tasks, so only expose this application privately. Do **not** turn on Tailscale Funnel or publicly expose port 8501. Consider account authentication before any wider distribution.
- Your original research data may be separately licensed. Yahoo/yfinance has personal/research restrictions; a business or commercial system should use a permitted market-data license. Historical options NBBO feeds often require paid entitlements.
- The existing official AION gold/navy logo and research tabs remain unchanged. The installer never touches `.env`, `aion-local.env`, SnapTrade credentials, trading orders, or the signal journal.
- The original source code is derived from the user's uploaded `server.py`, `engine_v2.py`, `market_v2.py`, `options_v2.py`, `index.html`, `app.js` and `style.css`; only local imports and nested `/lab` asset/API paths are adjusted.

If the installer says **"Research Suite must be installed first"**, install your previous `AION_Research_Suite_Mobile_Upgrade.zip` using its installer before repeating the unified upgrade. This patch deliberately avoids overwriting unknown dashboard layouts.
