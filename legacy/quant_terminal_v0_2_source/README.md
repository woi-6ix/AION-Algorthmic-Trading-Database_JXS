# AION Quant Terminal — v0.2 Stock + 0DTE Research Dashboard

Interactive website for AION Quant Labs algorithm research: market prices and candles, true imported backtests for KAIROS / DELPHI / BELLEROPHON, research-only example backtests, headline sentiment, and experimental HMM + GARCH + STORM regime analysis.

**Status: local deployable prototype, not hosted. No financial order execution.**


## What's new in v0.2 (stock-bar + 0DTE backtesting)

**This release adds runnable functionality, not just interface mockups.** It passed the included offline automated tests, but has not been validated against your historical TradingView Pine Strategy Tester fills or a live option quote subscription.

### Upgrade an existing Windows installation without losing backtests

1. **Stop the old server** (`Ctrl+C`) and close the Command Prompt.
2. Extract this ZIP. **Copy its updated program files and `static/` folder into your existing `aion-quant-terminal` directory**, accepting overwrite for program files. **Do not delete** the existing `data/` directory or your `aion.sqlite3` file.
3. Double-click `start_windows.bat` again; its diagnostics/logging remain intact. The existing `.venv` is reused and `pip` updates packages if needed.
4. Browse to `http://127.0.0.1:8000`. The new **Historical data** and **Stock + 0DTE engine** sections are above the previous backtest performance cards.

**Fresh install:** Extract the ZIP to a normal folder and double-click `start_windows.bat`. Python 3.12 64-bit is recommended. Unlike a hosted product, keep the console window open while using AION.

### 1. Load historical stock OHLCV (automatic, or CSV)

At the top of the website choose ticker, timeframe (1m, 2m, 3m, 5m, 15m, 30m, 1h, 1d, 1wk), lookback and optional start/end dates. In **Historical data**, choose a provider and **Download / cache bars**. These are saved to local SQLite (`data/aion.sqlite3`), so you can reuse them after closing the terminal.

- **Auto:** Uses your configured Massive API key when available, otherwise Yahoo Finance. No secret is required for Yahoo. This choice is explicit in the response source label.
- **Yahoo:** Automatic recent price candles, but the provider limits 1m/3m to roughly 7 days and most intraday intervals to roughly 60 days. Older intraday backtests cannot be reconstructed by repeating calls. Yahoo also may delay prices, change data policy, or restrict automated access.
- **Massive:** Supports custom stock date ranges, subject to your **own account's** exchange-data entitlements, historical access and request limits. The app requests **unadjusted** OHLCV. Subscriptions may be required for deep intraday history; data is not bundled.
- **TradingView sync:** Uses a **fresh snapshot sent by a separate authorized MCP worker** via `/api/tv-sync`. The ChatGPT/TradingView account authorization is *not* transferable to your local website. It is not a general TradingView Strategy Tester API.
- **Local cache:** Use previously downloaded or imported actual bars. For provider CSV imports, use the `OHLCV CSV template` link (`timestamp,open,high,low,close,volume`, with ISO timestamps and timezone offsets).

The app returns the **actual first and last bar timestamps and bar count**; it does not claim uninterrupted coverage and it never fills entire missing trading days with fake candles. For research, check provider-adjustment policy, corporate actions, RTH vs ETH, and timestamp semantics. For intervals other than 1m/2m/3m etc., resampling can be added in later revisions.

### 2. Run the *core* KAIROS V5 Python port on stock bars

Click **Run stock backtest**. The engine is based on the source repository [`KAIROS_LC_Swing_Engine_V5_JXS_918.pine`](https://github.com/woi-6ix/KAIROS-Lorentzian-ML-Swing-Engine_JXS/blob/main/KAIROS_LC_Swing_Engine_V5_JXS_918.pine).

The current port covers the following verified-by-source **formula shapes and configurable gates**: rational-quadratic/Gaussian trailing kernel flip, optional kernel smoothing, ATR-normalized slope filter, volatility-push ATR/range/volume score and directional candle, EMA 200/SMA 200 switch, ATR stop (default 1.5 × ATR), profit target (default **0.4R as in the current V5 Pine file**), opposite kernel-flip exits, NY intraday trading session and forced intraday liquidation. Conservative stop-before-target priority and extra slippage/commissions are included.

**Crucially, this is NOT an exact, fully replicated KAIROS V5 backtest.** The imported `jdehorty/KernelFunctions/2` weighting/startbar behavior has not been trade-for-trade validated, and the imported `jdehorty/MLExtensions/2` normalization, LC chronological ANN vote classifier, original LC regime gate and V5 walk-forward HMM **are not ported as entry filters**. Pine `process_orders_on_close=true` is approximated using bar-close signals/fills, not true tradeable historical bid/ask fills. Also, your Pine input `entryQuantity=2500` is a **number of shares/contracts**, whereas the website input defaults to a **$2,500 stock share notional** to avoid silently creating 2,500-SPY-share exposure. These are different configurations; do not compare their P&Ls directly.

For exact KAIROS, DELPHI or BELLEROPHON Pine results, continue importing the authentic **TradingView Strategy Tester List of Trades** CSV. To reuse the **exact trade timing/direction** for options bid/ask replay, export or transform your own Pine entry/exit events into a signal CSV, below. Do not mistake a user-imported actual Pine result for an independently verified run.

### 3. 0DTE historical options backtesting: quote-driven, not theoretical

The 0DTE section accepts **real two-sided historical NBBO option quotes** in `timestamp,contract,bid,ask` CSV format or can attempt provider retrieval from your own Massive Options account when **Auto-fetch Massive quotes** is checked. Neither an account nor actual market quotes are supplied with this repository. The system refuses to invent premiums where actual quote records are absent.

1. Run a KAIROS V5 core stock backtest above, **or** import actual Pine strategy entry/exit events in the signal template. Signal CSV columns: `entry_at,exit_at,side,underlying_entry_price` plus optional `contract` for a specific OCC symbol (e.g. `O:SPY261001C00700500`). Use full timestamps with timezone offsets.
2. Import historical options bid/ask CSV from your provider, or configure your own Massive API key below and enable automatic quote retrieval. The named date/contract MUST exist in the feed, and the option contract expiration MUST match its signal date.
3. Select **Selected KAIROS core stock run** or **Imported Pine signals**, choose number of option contracts, and select **Automatic ATM strike** (currently SPY/QQQ/IWM) or **Exact contract in imported signals**.
4. Select **Underlying signal exit** or an experimental **Observed option-bid premium stop/target**. In the latter, the stop is a percent of entry ask and target is `entry ask + stop amount × R`; actual observed option **bid** crossing the threshold triggers liquidation at the observed bid, never an assumed stop/limit price. All modes flatten on or before the underlying signal's exit timestamp if a fresh quote is available.
5. Click **Run 0DTE replay**. The result reports requested signals, matched option fills, skipped signals and (up to 30) missing-data/error reasons. Only completed, quote-covered trades contribute to P&L. Missing quotes can lead to **selection bias**; do not compare the reported P&L with an unfiltered historical strategy return without checking coverage.

**Pricing assumptions:** Long call on LONG signals, long put on SHORT signals, standard US equity options multiplier **100**, pay observed historical ask at entry, receive observed bid on exit, configurable side fees (default $0.65 per contract) and $0.01/contract premium-side slippage (both sides). Quotes must be timestamped within 60 seconds *after* requested signal entry/exit; delayed fills, quote gaps and live execution differences remain. A sampled quote touching a premium target does not prove a real order would fill. No IV surface, spread execution model, exercise/assignment, option Greeks, early exits when quotes disappear, stop order rejection, or broker/live-order handling.

**Current 0DTE constraints:** US-listed option contracts with same-day expiry on the signal date, automatically selected ATM strike for SPY/QQQ/IWM only; maximum 20 unique contracts per automatic provider quote request and 1,000 underlying signals per replay. Other stock symbols can be researched in the **stock-bar** engine; automatic 0DTE contract discovery is intentionally narrower. Historical option quote endpoints require appropriate data plans and may not include very old dates. Deep history can be costly and may require pagination over large datasets. The user's TradingView MCP provides **stock price bars, not historical option NBBO quote tapes**.

### 4. Configure the optional Massive market-data provider on Windows

1. In the application folder, make a **copy** of `aion-local.env.example` and rename it to `aion-local.env`.
2. Open `aion-local.env` in Notepad and set `AION_MASSIVE_API_KEY=YOUR_OWN_KEY` with **no quotes**, then save it. Do not send this file or upload it to GitHub. It is ignored by `.gitignore`.
3. Stop/restart `start_windows.bat`, select **Massive API** or **Auto**, and download a date range. Your options subscription also must include historical quotes, or option quote auto-download will report missing-entitlement errors. No paid subscription is provisioned by this application.

The app never sends your provider API key to browser JavaScript or includes it in the ZIP. Calls to Massive are initiated by the local Python backend.

### Known limitations / parity checklist

- This application does **not** automatically execute Pine in TradingView, export TV Strategy Tester runs, or share this chat's MCP OAuth credentials.
- Full LC/ML/HMM gate parity with KAIROS V5, and any runnable Python Delphi/Bellerophon implementation, **remain unimplemented**. Their exact results can be imported; their actual signal timestamps can drive 0DTE quote replay. This is deliberate rather than returning falsely precise metrics.
- Stock engine uses bar OHLC highs/lows and a conservative stop-first assumption when both stop and target occur in one bar. No tick order sequence or actual spread, market impact, rejected orders, or exact Pine fill parity.
- 0DTE quote replay is a long-premium **historical scenario**, not a full option market simulator. Delayed fill times and missing data can materially change P&L. Market holidays, liquidity, odd strike increments and contract changes require validation.
- HMM/GARCH/STORM dashboard models are exploratory descriptive estimates, not production trading filters or proof of future performance. STORM in this app is a custom composite, and HG remains a VADER headline prototype.
- This project is an unsecured **local application**. Do not deploy on the public internet without login, HTTPS, rate limiting, upload constraints, CSRF protections, CORS review and separate credentials per user.

### New v0.2 API routes

| Route | Role |
|---|---|
| `GET /api/data/status` | Cached bar and option contract count |
| `POST /api/data/download` | Retrieve OHLCV via Yahoo, Massive, cached bars or separate TV sync |
| `POST /api/data/import` | Upload genuine stock OHLCV CSV |
| `GET /api/data/template.csv` | Stock CSV example schema |
| `POST /api/strategy/run` | Run KAIROS V5 **core-port** stock-bar backtest |
| `POST /api/signals/import` | Store Pine event timestamps for options replay |
| `GET /api/signals` | List stored signal sets |
| `GET /api/signals/template.csv` | Pine event CSV example schema |
| `POST /api/options/quotes/import` | Import timestamped historical option NBBO bid/ask quotes |
| `GET /api/options/quotes/template.csv` | Option quote CSV example schema |
| `POST /api/options/run` | Replay underlying signals on actual same-day option quotes |

Test offline: `python -m pytest -q` (requires pytest). No live market-provider connection was used in these unit tests. You must verify your own provider login and date entitlements on your own computer.

---

## Quick start

Requires Python 3.11+ and internet for market data. On Windows, **Extract All** (do not run from inside the ZIP), then double-click `start_windows.bat`. It opens a persistent diagnostic Command Prompt that does not disappear after startup errors. On macOS/Linux, run `./start_mac_linux.sh`. Alternatively:

```bash
python -m venv .venv
# Windows PowerShell: .venv\Scripts\Activate.ps1
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
uvicorn server:app --reload --host 127.0.0.1 --port 8000
```

Open **http://127.0.0.1:8000**. In a production deployment use HTTPS, proper authentication, and a persistent DB volume.

### Windows startup troubleshooting

If the terminal closes unexpectedly, use the updated `start_windows.bat`, which opens a persistent command prompt. It runs `run_aion_windows.bat` and writes Python installation and server errors to `aion-startup.log` in the same folder. Send the log when requesting help. If no log appears, open a Command Prompt in the extracted folder, run `cmd /k start_windows.bat`, and read the resulting error. Running inside an unextracted ZIP is not supported.

The revised `start_windows.bat` automatically tries `py -3`, `python`, and `python3` to create the virtual environment. The Windows `py` launcher is optional. If the script reports that it cannot find a usable Python installation, open **Command Prompt** and run `python --version`. If that does not print Python 3.11 or newer, install Python 3.12 from https://www.python.org/downloads/windows/ and enable **Add python.exe to PATH** in the installer. Reopen the terminal after installing, then double-click `start_windows.bat` again. If a previous `.venv` environment is broken, delete that local `.venv` folder first and re-run the script.


## Working features

- Type any Yahoo-supported symbol (SPY, QQQ, AAPL, NVDA, TSLA, BTC-USD), choose a bar interval and lookback, see real OHLCV data from Yahoo Finance via `yfinance`. Refresh every 120 seconds while the page is visible. **Prices may be delayed.** On Yahoo, 1m history usually only covers ~7 days, and intraday histories are limited. The app explicitly rejects insufficient requested periods instead of pretending to have them.
- 3-minute bars are aggregated from 1-minute data. For deeper 0DTE studies, use a paid tick/option dataset; shares OHLCV cannot accurately model option premium, IV crush or bid-ask fills.
- View real imported TradingView Strategy Tester closed-trade P&L. Select an algorithm, ticker/timeframe, **Import CSV**, then compare performance and equity. Saved runs are retained locally in SQLite. Use the template at `/api/trades-template.csv` if the exported format isn't detected. The simple CSV schema is `date,side,pnl` (USD per closed trade), optionally with negative P&Ls. Import does not claim to verify the Strategy Tester, underlying prices, or option fills.
- Two optional educational baselines — `NW Flip · research` and `ORB · research` — can be run on downloaded OHLCV bars. These trade **shares**, not options, and are **not** exact conversions of your Pine Script implementations. Backtests use next-bar-open entry and conservative stop-first processing when both bracket levels appear within one bar. A bare-bones commission and slippage model is included, but gap fills, partial fills and intrabar sequence are not modeled.
- Three-state `hmmlearn` Gaussian HMM on per-bar returns and 20-bar rolling realized volatility, with probability and latest most-likely state. A GARCH(1,1) model estimates one-bar-ahead conditional sigma. These are statistical estimates with estimation uncertainty, not directional predictions.
- STORM custom composite score: `0.6 × P(HMM high-vol) + 0.4 × GARCH vol percentile`. Interpretation: CALM `<33`, NORMAL `33–67`, STORM `≥67`. **This is a newly specified research composite**, not a verified replication of any outside STORM code or proprietary implementation.
- “HG sentiment” displays a **VADER headline polarity prototype**; it is not a validated HG indicator. It reports unavailable when news is unavailable, and discloses the model and headline count. It does not fetch or score entire news articles.

## TradingView MCP and deployment boundary

Your TradingView MCP **is connected to ChatGPT**, and in this chat TradingView OHLCV, technical snapshots, and news have been fetched successfully. That OAuth authorization is scoped to the ChatGPT client and **cannot automatically be reused by an independent website**. TradingView's official MCP beta covers OHLCV, news, watchlists, technicals, alerts, etc., but **does not expose the Pine Strategy Tester or saved Pine source**. Official docs: https://www.tradingview.com/mcp/docs

This standalone website therefore uses independently authorized market data providers for automatically updating market data. For exact performance, export the **List of Trades** CSV from TradingView Strategy Tester, or implement/port each algorithm into a Python backtester and validate trade-for-trade. Do not assume a share-based proxy means 0DTE SPY options results match.

### Future authenticated TradingView data integration

Add a separate user-initiated TradingView OAuth 2.1 flow in the website/server, then call the official streamable HTTP MCP endpoint `https://mcp.tradingview.com/mcp` from your own backend (never from client browser JS or with ChatGPT's tokens). A polling/queue worker should fetch historical bars/news with caching and API-rate control. MCP official rate limit is about 100 calls/minute/user in beta; data freshness and exchanges' permissions vary.

## Security and development caveats

- There is **no login** in this local prototype. Anyone who can access the server can upload a backtest. Do not expose port 8000 publicly without authentication, upload controls, HTTPS, CSRF safeguards, per-user storage, and rate limits.
- Import supports P&L fields in a conventional CSV or a TradingView export with an exit Type and Net P&L column. Review totals after importing; source format varies.
- No live brokerage orders, comprehensive option-chain discovery, options Greeks, portfolio positions, or forward-test trading sync. Historical NBBO option quotes can now be loaded for a long-premium 0DTE replay; this is NOT an options chain or a model of every execution detail.
- In-sample HMM labels, kernel baseline thresholds and VADER polarity are **research**, not validated signals. For performance claims use unseen out-of-sample dates, realistic commissions/spreads, and walk-forward testing.

## API

- `GET /api/health`
- `GET /api/algorithms`
- `GET /api/overview?symbol=SPY&interval=5m&period=1mo`
- `GET /api/backtests`
- `POST /api/backtests/import` (multipart `file`, `algorithm`, `symbol`, `timeframe`, `label`)
- `POST /api/backtests/research?symbol=SPY&interval=5m&period=5d&algorithm=NW_FLIP_RESEARCH&risk_usd=500`
- `GET /api/backtests/{run_id}`
- `GET /api/trades-template.csv`

## Test

```bash
pip install pytest
pytest -q
```

## Authenticated TradingView MCP data bridge (optional)

The FastAPI server supports a **separately authorized external worker** sending market data from TradingView MCP. The worker must be a client that has itself completed TradingView OAuth; this project does **not** read ChatGPT's OAuth tokens.

1. Set a long random secret only in your server environment, e.g. `AION_TV_SYNC_TOKEN` (never in browser JS or GitHub).
2. From a separately authorized MCP worker, call TradingView `get_ohlcv` (columns or rows) and optionally `get_news` for the same symbol.
3. Have the worker POST to `/api/tv-sync` with header `X-Aion-Sync-Token: <your-server-secret>` and JSON with `symbol`, `interval`, `ohlcv` containing the **original TradingView tool output**, and optionally `news` containing the original `get_news` output. Use HTTPS and a private server.
4. Re-run the worker periodically (e.g., every 1–2 minutes). The dashboard uses a TV snapshot only if it was received within the last 4 minutes; otherwise it falls back to Yahoo Finance. A source badge states which feed was used.

Example JSON payload (illustrative **schema**, NOT market data):

```json
{
  "symbol": "SPY",
  "interval": "5m",
  "ohlcv": {"bars": {"t": [1700000000, 1700000300], "o": [100, 100.2], "h": [100.5, 100.8], "l": [99.7, 100.1], "c": [100.2, 100.6], "v": [9000, 11000]}},
  "news": {"data": {"headlines": []}}
}
```

This endpoint is **not** an OAuth login implementation; configuring an always-on authorized MCP worker is a separate deployment step. The 4-minute cache and provider-specific historical depth mean the app is near-live, **not** exchange-certified real-time data. The `GET /api/overview` response declares its actual source. The app does not claim to download Pine backtest reports via MCP.
