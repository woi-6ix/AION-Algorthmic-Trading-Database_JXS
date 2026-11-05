# AION Quant Labs — Research Suite v1 for Live Mobile Trading Dashboard

**Installs into your existing `C:\Users\sathw\AION-Execution` folder, and updates only the dashboard on port 8501.**

## What's included

- **Screener**: user-editable watchlist of up to 12 stocks/ETFs; daily close, 1D change, RSI(14), EMA20/50 trend, relative volume and 20-day realized volatility. Click any symbol for daily close chart.
- **News & Sentiment**: Google News RSS headline search and VADER lexical scoring, clearly labeled as *not* the original HG sentiment model.
- **Regime Lab**: daily-bar GaussianHMM (when `hmmlearn` installed), GARCH(1,1) conditional volatility (when `arch` installed), and a clearly labeled **STORM-like proxy**, not the original STORM implementation.
- **Backtesting Hub**: checks whether your original AION Quant Terminal still runs on port 8000, and links to it via a separate **private Tailscale Serve port 8443**.
- **Original features preserved**: overview, KAIROS V5 signal history, execution journal, System Health, exact official AION logo, existing Tailscale 8501 mapping.

This is a new *research* implementation, not a byte-for-byte import of original Quant Terminal features: the original site's Python source was not provided. To port your actual HG and STORM algorithms or unify the exact backtesting UI, those source files are needed. No fabricated sentiment or regime predictions.

## Installation on Windows PowerShell

Download `AION_Research_Suite_Mobile_Upgrade.zip` into Downloads, then run:

```powershell
cd C:\Users\sathw\AION-Execution
Expand-Archive -Path "$env:USERPROFILE\Downloads\AION_Research_Suite_Mobile_Upgrade.zip" -DestinationPath . -Force
.\.venv\Scripts\python.exe -m pip install -r .\requirements-aion-research.txt
.\.venv\Scripts\python.exe .\install_aion_research.py
```

Optional **actual fitted HMM/GARCH models** (rather than unavailable states):

```powershell
.\.venv\Scripts\python.exe -m pip install -r .\requirements-aion-advanced-models.txt
```

If you already have the dashboard on 8501, stop **only that server** with Ctrl+C in its terminal (don't stop AION 8001 or the webhook 8002), then restart:

```powershell
.\.venv\Scripts\python.exe -m uvicorn aion_live_dashboard:app --host 127.0.0.1 --port 8501
```

Test locally: `http://127.0.0.1:8501/` and `http://127.0.0.1:8501/api/research/capabilities`.

Phone (Tailscale app connected): `https://jxs-hpx360.tailf4db21.ts.net/` — current private URL. Tailscale Serve still proxies 443 to 8501. No public Cloudflare Tunnel for dashboard access.

## Make original Quant Terminal's backtester available on your phone

Only if your original app still works on Windows at `http://127.0.0.1:8000/`, in another PowerShell window run:

```powershell
tailscale serve --https=8443 --bg http://127.0.0.1:8000
tailscale serve status
```

This should add a *second* private Tailscale address (same hostname, HTTPS port 8443), without replacing your dashboard's 443→8501 mapping. The **Backtesting** tab opens it. You may need to allow this HTTPS port in your tailnet settings/client and verify its certificate. Do not use Tailscale Funnel and do not expose 8000, 8001, or 8002 publicly.

## Important limitations and license notices

- **Not an order-execution feed.** This dashboard does not send trades or connect to SnapTrade credentials. Price bars are unofficial/daily, and may be delayed or missing. News scoring is neither realtime nor guaranteed to be ticker-specific.
- **Data license:** `yfinance` explicitly states that Yahoo Finance API is intended for personal use only. This package is for *local research/testing*. For AION Quant Labs as a commercial product, obtain an appropriate paid/licensed market-data/news provider before business deployment; merely running on Tailscale does not change third-party terms.
- Set `AION_RESEARCH_PROVIDER=off` in the process environment to disable external Yahoo price fetches. Re-launch dashboard to apply.
- HMM/GARCH estimates use daily price history and are not equivalent to your 3-minute KAIROS V5 Pine model. STORM is explicitly a proxy because original model source was not available. No dataset is substituted if provider fails: panels show errors/unavailable.
- The original backtest engine still runs as a separate site. The Backtesting Hub links to it rather than copying its proprietary code.
- Data cached for 10 minutes; each screener is limited to 12 ticker symbols to avoid overwhelming public endpoints.
- **No real or paper order placement** in these files. It is a read-only UI enhancement.

## Diagnostics

```powershell
Invoke-RestMethod http://127.0.0.1:8501/api/research/capabilities
Invoke-RestMethod http://127.0.0.1:8501/api/research/terminal
Invoke-RestMethod 'http://127.0.0.1:8501/api/research/screener?tickers=SPY,QQQ'
```

Research price feeds will return 503 or partial results if Yahoo is unreachable or rate-limits access. HMM/GARCH cards say 'Unavailable' until their optional packages are installed. The website itself remains usable without those feeds.
