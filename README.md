# AION Quant Labs — Algorithmic Trading Research & Paper Execution

Private, Windows-hosted **AION** platform for KAIROS V5 SPY intraday strategy signals, historical stock/0DTE option research, mobile monitoring, and **Alpaca Paper-only** options execution sized up to **$2,500 in option premium per entry** on KAIROS V5 alerts.

> **Status (Nov 5, 2026):** Code assembled from the AION packages built during development. The latest local Options Chart HTTP 400 patch is applied in this source snapshot, but the fix **has not yet been verified against live Alpaca data**. This repository is not a copy of any undisclosed files present only on the developer's Windows laptop. Real-money execution is **not implemented** and must remain disabled.

## System overview

| Component | Default | Purpose |
| --- | --- | --- |
| `aion_kairos_server.py` | `127.0.0.1:8001` | KAIROS V5 signal simulator; SQLite event tracking |
| `aion_tv_gateway.py` | `127.0.0.1:8002` | Authenticates TradingView alerts, relays to simulator |
| `aion_live_dashboard.py` | `127.0.0.1:8501` | Read-only monitoring and research base app |
| `aion_ltm_with_controls.py` | `127.0.0.1:8501` | Opt-in, token-protected Alpaca PAPER manual position controls and automation pause/resume |
| `aion_backtest_engine/` | mounted at `/lab/` | Stock/0DTE historical backtesting and CSV imports |
| `aion_alpaca_paper_panel.py` | mounted at `/alpaca-paper/` | Read-only options chain/chart & paper status |
| `aion_alpaca_paper_algo.py` | separate command-line worker | **Alpaca Paper** SPY 0DTE limit orders from accepted signals |
| `aion_execution_journal.py` | local SQLite | Only broker-confirmed option fills, P&L journal |
| `aion_research.py` | internal research endpoints | Screener, news sentiment, research-only regimes |

The webhook is exposed to TradingView through a separate HTTPS tunnel for **port 8002 only**. The dashboard is accessed privately over Tailscale; the laptop must be running. Never expose the dashboard, Alpaca keys, paper worker, or admin ports directly to the public internet.

## Quick start (Windows PowerShell)

```powershell
cd C:\Users\sathw\AION-Execution
# Clone into a NEW, CLEAN directory instead if you already run AION locally.
.\.venv\Scripts\python.exe -m pip install -r .\requirements-aion.txt
.\.venv\Scripts\python.exe -m pip install -r .\requirements-aion-unified.txt
.\.venv\Scripts\python.exe -m pip install -r .\requirements-alpaca-paper-algo.txt
```

Copy `.env.example` to a local `.env` and fill in **paper-only** API credentials. The repository's `.env.example` is a template; never overwrite an existing working `.env` or upload credentials. Initialize and start each service in **separate** PowerShell windows:

```powershell
# Terminal 1: KAIROS signal engine (local)
.\.venv\Scripts\python.exe -m uvicorn aion_kairos_server:app --host 127.0.0.1 --port 8001
# Terminal 2: authenticated TradingView gateway (local)
.\.venv\Scripts\python.exe -m uvicorn aion_tv_gateway:app --host 127.0.0.1 --port 8002
# Terminal 3: unified AION dashboard + PAPER-only controls (local)
.\start_aion_ltm_controls.ps1
```

Local dashboard: `http://127.0.0.1:8501/` · Backtests: `http://127.0.0.1:8501/lab/` · Options panel: `http://127.0.0.1:8501/alpaca-paper/`.

**Important:** Startup scripts and package upgrades are maintained for Windows convenience; do not run the historical installer scripts indiscriminately over an already working installation. See `docs/INSTALL_WINDOWS.md` before migrating an existing `AION-Execution` folder.

## Alpaca Paper worker

The worker is **separate** from the read-only dashboard. It is designed to use `https://paper-api.alpaca.markets` for every order operation; it does not submit to Alpaca Live, SnapTrade, Webull CASH or Webull MARGIN.

```powershell
.\.venv\Scripts\python.exe .\aion_alpaca_paper_algo.py check     # read-only paper account check
.\.venv\Scripts\python.exe .\aion_alpaca_paper_algo.py chain     # fetch today's SPY options quotes
.\.venv\Scripts\python.exe .\aion_alpaca_paper_algo.py status    # arm/kill status
.\.venv\Scripts\python.exe .\aion_alpaca_paper_algo.py disarm    # disable NEW paper entries
```

When validating options data, `.\.venv\Scripts\python.exe .\aion_alpaca_paper_algo.py chart --symbol <OCC_SYMBOL>` requests 3-minute historical option bars. On Alpaca's free options market-data plan, historical candles can lag real time; quotes are *indicative*, not OPRA-quality consolidated prices.

**Trading behavior:** KAIROS V5 LONG and SHORT alert fills select SPY 0DTE calls/puts; sizing floors the number of contracts under a **$2,500 total premium-debit cap** (25-contract secondary limit). Real KAIROS V5 exit, kernel-flip, reversal and session-cutoff alerts control closes; the old +30%/−25% premium exits have been removed. A separate 14:45 ET failsafe still attempts a close. See [KAIROS V5 $2,500 Paper Upgrade](README_AION_KAIROS_V5_2500_PREMIUM.md).

**Automated trading requires explicit user action:** `arm`, then `run`. Do not arm without successful tests of quote freshness, account permissions, order reconciliation, exit handling and webhook origin security. `disarm` prevents new entries but **does not cancel resting orders or close existing paper positions**. `AION_MODE=SIMULATION` is intentional even for the Alpaca Paper worker. The worker processes accepted events from the local simulator, not from the dashboard browser.

## AION LTM — Alpaca PAPER manual controls (opt-in)

**Stable token startup:** Launch port 8501 with `start_aion_ltm_controls.ps1` instead of manually regenerating `AION_CONTROL_TOKEN`. The script keeps a per-user secret under `%LOCALAPPDATA%\AION\ltm-control-token.txt`, copies it to the clipboard, and prevents a second server from occupying the port. After a browser refresh, recopy the same secret with `Get-Content "$env:LOCALAPPDATA\AION\ltm-control-token.txt" -Raw | Set-Clipboard`. See [persistent token guide](README_AION_LTM_PERSISTENT_TOKEN.md). This changes dashboard authentication only and does not place orders.


The `aion_ltm_with_controls.py` wrapper retains the original dashboard UI, official logo, `/lab/` backtesting and read-only `/alpaca-paper/` charts. It adds authenticated `/api/alpaca-control/` endpoints and a floating control panel to pause (disarm), resume (arm), close a selected PAPER position, or request account-wide PAPER position liquidation. This is **PAPER ONLY**; no live-trading API host or trade credentials are included in browser code.

**Installation:** See [AION LTM manual controls setup](README_AION_LTM_PAPER_CONTROLS.md). On an existing Windows AION-Execution install, use the backup-first `install_ltm_worker_arm_patch.py` compatibility patch before using Resume. The worker's normal CLI requires a TTY to arm; the patch accepts only approval from an authenticated dashboard process and preserves the worker's account, risk and order checks. Never publish `AION_CONTROL_TOKEN` or run the control service publicly without additional authentication and transport protection.

Pause and manual close stop **new orders and automated exit handling** because the worker disarms; existing positions do not close automatically on pause. Resume *re-arms* but **does not restart a stopped worker**. Broker close requests are not evidence that fills completed. Test with PAPER and no open positions before attempting close operations; verify order status in Alpaca.

## Supervised PAPER worker (Windows)

Opt-in `aion_paper_supervisor.py` can run `aion_alpaca_paper_algo.py run` independently of the browser and the dashboard terminal, under a per-user Windows Task Scheduler logon task created by `install_aion_paper_supervisor.ps1`. It starts a worker **only when already armed, account-bound, and not kill-switched**. A manual worker is never duplicated or silently taken over, and unexpected worker exits trigger the existing kill switch instead of restarting trading indefinitely. The LTM now displays supervisor status and can request the registered task to start when you press Resume.

**Warning:** If the PAPER bot is already armed at Windows sign-in, installing this task authorizes it to resume PAPER trading after you log in. It never automatically arms the bot, and it cannot manage orders while the PC is off, asleep or disconnected. Read [Windows supervisor setup and safeguards](README_AION_PAPER_SUPERVISOR.md) before registering or starting the task. Keep it PAPER-only.

## Research/backtesting caveats

- Stock 3-minute history is available through imported bars/provider downloads; it is **not** a complete reconstruction of TradingView's Pine KAIROS V5 indicators.
- Options historical replay requires real bid/ask history (CSV or separately entitled data provider); connecting a paper brokerage does not populate that historical quote database.
- The free Alpaca indicative options feed is for development and forward testing, not reliable execution-quality performance estimates.
- See `README_AION_UNIFIED.md` and `docs/ARCHITECTURE.md` for limitations.

## Source layout

- **Root Python and scripts:** assembled current unified AION source.
- `aion_backtest_engine/`: integrated original v0.2 FastAPI engine.
- `backtest_bundle/`, `unified_parts/`, `research_components/`: legacy integration/upgrade build materials retained for reproducibility.
- `static/`: user-facing AION UI and branded logo.
- `legacy/quant_terminal_v0_2_source/`: unmodified original independent Quant Terminal source.
- `legacy/synthetic_paper_demo/`: earlier fixed-price simulated paper broker demonstration.
- `legacy/snaptrade_alpaca_connection/`: earlier SnapTrade Paper connection helper, **not** the live worker.
- `patches/release_archives/`: historical patch ZIPs. Some are superseded; use the current root files rather than stacking patches blindly.
- `docs/`: architecture, Windows setup, security, and patch inventory.

## Security

**This GitHub repository is public.** Never commit `.env`, API secrets, TradingView webhook JSON containing the secret, SnapTrade credentials, brokerage account numbers, SQLite databases, fill journals, or logs. `.gitignore` blocks these categories, but always review `git status` before committing.

**Do not interpret a TradingView strategy alert as a brokerage fill.** The paper worker may have pending/partial/unknown orders; reconcile with Alpaca before making decisions. See `SECURITY.md`.

## Development status

- Latest implemented: direct Alpaca Paper SPY 0DTE options selection, order submission worker, journal reconciliation, options-chain data panel, and a patch for historical bars HTTP 400, plus the $2,500 KAIROS V5 signal-aligned paper execution upgrade.
- Verified offline and locally in the development environment; *not* remotely deployed or validated against the user's running Windows copy in this GitHub import.
- Known gaps: options historical chart fix not yet verified against your account, free indicative option quotes are not reliable executable OPRA, limit exits may not fill, temporary Cloudflare tunnels are fragile, order/position reconciliation still requires supervised paper testing, and the laptop needs to remain running.

### November 5, 2026 — Alpaca options-chart preflight

If historical SPY options bars return `Alpaca DATA HTTP 400`, use the **read-only** [`aion_option_preflight.py`](aion_option_preflight.py) and the [preflight guide](README_AION_PAPER_PREFLIGHT.md) to diagnose the historical quote entitlement and GET parameters without arming the paper bot. The ZIP is archived in [`patches/release_archives/`](patches/release_archives/). **No new brokerage orders** are placed by this diagnostic.

### November 5, 2026 — historical SPY options bars HTTP 400 repair

The Alpaca historical option-bars API rejects `feed=indicative` (`unexpected query parameter(s): feed`); the historical bar loader and chart preflight now omit that parameter, cap requests conservatively for a Basic-plan delay, and label historical *trade bars* separately from indicative latest quotes. See [chart fix and installation](README_AION_OPTIONS_BARS_FEED_FIX.md). The **$2,500 total premium** KAIROS V5 paper-order logic and account guards are unchanged; this release was tested **offline** and still requires a successful Alpaca call on the Windows host. Broker paper automation stays **disarmed** until verified.

## License

This repository is covered by its existing [MIT License](LICENSE), except where separate third-party terms apply.