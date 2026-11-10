# ARGUS Terminal — AION's Live Trading Monitor

**ARGUS (AION LTM)** is the private, Windows-hosted monitoring and control terminal for **AION Quant Labs**. It observes 3-minute SPY strategy alerts from [**KAIROS — Lorentzian ML Swing Engine**](https://github.com/woi-6ix/KAIROS-Lorentzian-ML-Swing-Engine_JXS), signal ingestion, execution state, broker-confirmed fills, system health, and research/backtesting. The implemented automated order route is **Alpaca PAPER only**, with a **$2,500 maximum option-premium debit per entry**; no live brokerage order path is deployed.

> **Deployment snapshot — November 10, 2026:** ARGUS is operating locally on the owner's Windows machine. The dashboard and other AION services have been launched as background scheduled tasks; ngrok has been migrated to an automatically starting Windows service. PAPER entry/exit orders have been tested previously, but automated operation remains experimental and requires monitoring. A temporary Alpaca account-API connection failure overnight on November 10 stopped the PAPER worker and activated its kill switch; it was manually reconciled and rearmed later. Do **not** infer worker health, current arming, or live orders from this dated README: inspect the local heartbeat and Alpaca broker account directly.

## Why is it called ARGUS?

**Argus Panoptes** ("all-seeing Argus") is the watchful guardian of Greek mythology, often depicted with a hundred eyes. The name reflects the terminal's role: **observe every signal, execution, account state and infrastructure warning without mistaking a signal for a filled trade**. ARGUS is the *watchtower*; [**KAIROS**](https://github.com/woi-6ix/KAIROS-Lorentzian-ML-Swing-Engine_JXS) is the strategy that identifies trading opportunities; **AION Quant Labs** is the parent organization. The existing official circular AION logo is retained, and existing `aion_*` filenames remain unchanged to avoid breaking services, task registrations and API paths.

| Brand | Role |
| --- | --- |
| **AION Quant Labs** | Parent quant research and algorithmic trading organization |
| **[KAIROS](https://github.com/woi-6ix/KAIROS-Lorentzian-ML-Swing-Engine_JXS)** | SPY 3-minute ML strategy and TradingView alert source |
| **ARGUS Terminal** | Tailnet-restricted monitoring, research, diagnostics and authenticated Alpaca PAPER operator controls |

## System overview

| Component | Default | Purpose |
| --- | --- | --- |
| `aion_kairos_server.py` | `127.0.0.1:8001` | KAIROS V5 signal simulator; SQLite event tracking |
| `aion_tv_gateway.py` | `127.0.0.1:8002` | Authenticates TradingView alerts, relays to simulator |
| `aion_live_dashboard.py` | `127.0.0.1:8501` | ARGUS-branded monitoring and research base app |
| `aion_ltm_with_controls.py` | `127.0.0.1:8501` | ARGUS website wrapper with token-protected PAPER position controls and pause/resume |
| `aion_backtest_engine/` | mounted at `/lab/` | Stock/0DTE historical backtesting and CSV imports |
| `aion_alpaca_paper_panel.py` | mounted at `/alpaca-paper/` | Read-only options chain/chart & paper status |
| `aion_alpaca_paper_algo.py` | supervised worker process | **Alpaca PAPER** SPY 0DTE limit orders from accepted signals |
| `aion_paper_supervisor.py` | `AION-Alpaca-Paper-Supervisor` scheduled task | Starts the worker only when armed, account-bound and not kill-switched; no automatic re-arm after failure |
| `aion_ltm_background.py` | `AION-LTM-Dashboard` scheduled task | Serves ARGUS without keeping a terminal or browser tab open |
| ngrok v3 Windows service | Automatic Windows service → `127.0.0.1:8002` | Public HTTPS ingress for authenticated TradingView POSTs **only** |
| Tailscale Serve | Tailnet-only HTTPS → `127.0.0.1:8501` | Private VPN-like access to ARGUS from approved Tailnet devices |
| `aion_execution_journal.py` | local SQLite | Only broker-confirmed option fills, P&L journal |
| `aion_research.py` | internal research endpoints | Screener, news sentiment, research-only regimes |

## Access model — Tailnet-restricted ARGUS, separate public webhook

**ARGUS dashboard access is private:** the website listens on **localhost port 8501**. The operator reaches it locally or through **Tailscale Serve over the private Tailnet**, which requires an authenticated device/user with Tailnet access (subject to Tailnet ACLs). The Tailnet hostname is intentionally not published here. The ARGUS operator's PAPER controls also require the separate `AION_CONTROL_TOKEN` stored locally outside this public repository. Neither the dashboard nor its control API should be published via the public ngrok tunnel.

**The TradingView webhook is *not* VPN-only.** A separate ngrok HTTPS endpoint exposes **only the TradingView gateway on port 8002** to allow TradingView's external servers to deliver strategy alerts. The gateway checks the JSON shared secret, strategy, symbol, timeframe, action and timestamp, then forwards accepted events to the **localhost-only** receiver on port 8001. Its HTTP 200 acknowledgment means *the signal was received*, not that an options order was sent or filled. A shared secret alone does not authenticate the actual sender; rotate compromised secrets and add hardened origin controls before considering real-money execution.

| Network surface | Exposure | Authorization / purpose |
| --- | --- | --- |
| ARGUS `:8501` | Localhost + Tailscale Tailnet only | Tailnet membership/ACLs; PAPER control actions additionally require `AION_CONTROL_TOKEN` |
| KAIROS receiver `:8001` | Localhost only | Signal database/event-state engine; **no** brokerage submission |
| TradingView gateway `:8002` | Localhost; public through ngrok HTTPS | Shared-secret authenticated POST `/tv-webhook`; **not** a dashboard/VPN endpoint |
| Alpaca API | Outbound HTTPS from PAPER worker only | Local PAPER credentials; hard-coded `paper-api.alpaca.markets` for trading |
| SnapTrade / Webull LIVE | **Not enabled** | Future architectural integration only; no live orders in this repo |

## Workflow A — implemented KAIROS → Alpaca PAPER

```mermaid
flowchart TD
    TV["TradingView: KAIROS V5<br/>3-minute SPY order-fill alert"] -->|"HTTPS POST + shared secret"| N["ngrok HTTPS ingress<br/>public webhook only"]
    N --> GW["AION TV Gateway<br/>127.0.0.1:8002"]
    GW -->|"Validate and translate"| RC["KAIROS signal receiver<br/>127.0.0.1:8001"]
    RC --> DB[("Signal events<br/>SQLite")]
    DB --> WRK["AION PAPER execution worker"]
    SUP["Windows PAPER supervisor"] -->|"Only when armed + bound<br/>and kill switch absent"| WRK
    WRK --> GATE{"Risk and broker checks pass?<br/>Fresh signal/quote, position,<br/>session, spread and premium cap"}
    GATE -->|"No / ambiguous"| BLOCK["Skip or fail closed<br/>operator reconciliation"]
    GATE -->|"Yes"| API["Alpaca PAPER API<br/>SPY 0DTE BUY_TO_OPEN / SELL_TO_CLOSE"]
    API -->|"Orders, broker-confirmed fills"| JOURNAL[("Execution journal<br/>and PAPER state")]
    JOURNAL --> DASH["ARGUS Terminal<br/>localhost :8501"]
    RC -->|"Signal status"| DASH
    BLOCK -->|"Health / exception state"| DASH
    VPN["Authorized Tailscale<br/>Tailnet device"] -->|"Private HTTPS"| DASH
```

**Implemented safeguards:** explicit arming and an independent kill switch, account binding, duplicate-worker locking, broker reconciliation and deterministic order IDs, a **55-second signal freshness limit**, indicative-quote freshness of **120 seconds**, maximum option spread of **$0.40 and 35%** of ask, **$2,500 total premium per entry** and **25 contracts maximum**, **09:31–14:45 ET** entry window (14:45 exclusive), EXIT processing through **15:02 ET**, and a **14:45 ET attempted emergency close**. A failed or unknown submission must be investigated; the 14:45 attempt is not guaranteed to fill. No verified daily loss cap is implemented. Trading is PAPER-only; simulated fills are not evidence of real execution quality.

## Workflow B — proposed SnapTrade / Webull LIVE architecture (NOT DEPLOYED)

```mermaid
flowchart TD
    TV2["TradingView: KAIROS V5 signals"] --> SIG["Signal validation and journaling"]
    SIG --> LGR["Proposed isolated LIVE order router<br/>(not implemented)"]
    LGR --> AUTH{"Independent LIVE authorization,<br/>broker permissions, controls,<br/>order-state reconciliation"}
    AUTH -->|"No or not validated"| DENY["BLOCK LIVE order<br/>retain PAPER-only operation"]
    AUTH -.->|"Future: only if broker and options support verified"| SN["SnapTrade broker integration<br/>(proposed)"]
    SN -.->|"Only if trading API supports it"| WEB["Webull LIVE brokerage account<br/>(proposed; not connected to worker)"]
    AUTH -.->|"Alternative: after separate API authorization"| WAPI["Direct Webull execution adapter<br/>(not built)"]
    WAPI -.-> WEB
    WEB -.->|"Future order/fill confirmations"| AUDIT["Separate LIVE audit, reconciliation,<br/>risk controls and kill switch"]
    AUDIT -.-> DASH2["ARGUS Terminal<br/>future LIVE status view"]
```

**This is a roadmap diagram, not an enabled broker connection.** SnapTrade may provide brokerage connectivity, but **Webull account linking, 0DTE option order placement, account entitlements, supported order types and live execution permissions must be confirmed**. Do not assume SnapTrade can route Webull options orders or that a TradingView/Webull plugin is a production broker gateway. Any LIVE adapter must be isolated from the existing PAPER worker, require a separate explicit enablement and identity verification, and add tested position limits, daily loss limits, idempotency, broker/market data validation, audit logging, emergency exits and recovery procedures. **No live brokerage credentials or endpoints are wired into the current execution path.**


## Quick start (Windows PowerShell)

```powershell
cd C:\Users\sathw\AION-Execution
# Clone into a NEW, CLEAN directory instead if you already run AION locally.
.\.venv\Scripts\python.exe -m pip install -r .\requirements-aion.txt
.\.venv\Scripts\python.exe -m pip install -r .\requirements-aion-unified.txt
.\.venv\Scripts\python.exe -m pip install -r .\requirements-alpaca-paper-algo.txt
```

Copy `.env.example` to a local `.env` and fill in **paper-only** API credentials. Never overwrite an existing working `.env` or upload credentials. **The following manual commands are for a new development setup, not the currently deployed background configuration.** On the operator's Windows machine, the receiver, gateway, ARGUS dashboard and PAPER supervisor already use independent Task Scheduler tasks, and ngrok uses a Windows service; do not launch second copies on the same ports:

```powershell
# Terminal 1: KAIROS signal engine (local)
.\.venv\Scripts\python.exe -m uvicorn aion_kairos_server:app --host 127.0.0.1 --port 8001
# Terminal 2: authenticated TradingView gateway (local)
.\.venv\Scripts\python.exe -m uvicorn aion_tv_gateway:app --host 127.0.0.1 --port 8002
# Terminal 3: unified AION dashboard + PAPER-only controls (local)
.\start_aion_ltm_controls.ps1
```

Local ARGUS dashboard: `http://127.0.0.1:8501/` · Backtests: `http://127.0.0.1:8501/lab/` · Options panel: `http://127.0.0.1:8501/alpaca-paper/`. Remote monitoring is available only to authorized Tailscale Tailnet devices.

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

## ARGUS Terminal — Alpaca PAPER manual controls (opt-in)

**Stable token startup:** Launch port 8501 with `start_aion_ltm_controls.ps1` instead of manually regenerating `AION_CONTROL_TOKEN`. The script keeps a per-user secret under `%LOCALAPPDATA%\AION\ltm-control-token.txt`, copies it to the clipboard, and prevents a second server from occupying the port. After a browser refresh, recopy the same secret with `Get-Content "$env:LOCALAPPDATA\AION\ltm-control-token.txt" -Raw | Set-Clipboard`. See [persistent token guide](README_AION_LTM_PERSISTENT_TOKEN.md). This changes dashboard authentication only and does not place orders.


The `aion_ltm_with_controls.py` wrapper retains the ARGUS-branded dashboard UI, original AION Quant Labs logo, `/lab/` backtesting and read-only `/alpaca-paper/` charts. It adds authenticated `/api/alpaca-control/` endpoints and a floating control panel to pause (disarm), resume (arm), close a selected PAPER position, or request account-wide PAPER position liquidation. This is **PAPER ONLY**; no live-trading API host or trade credentials are included in browser code.

**Installation:** See [AION LTM manual controls setup](README_AION_LTM_PAPER_CONTROLS.md). The current repository worker already contains the authenticated dashboard arming guard; use the backup-first `install_ltm_worker_arm_patch.py` only when upgrading an older, compatible historical installation, never blindly over the current worker. The worker's normal CLI requires a TTY to arm; the patch accepts only approval from an authenticated dashboard process and preserves the worker's account, risk and order checks. Never publish `AION_CONTROL_TOKEN` or run the control service publicly without additional authentication and transport protection.

Pause disarms the worker, stopping further automatic processing, including strategy-driven exits; existing positions do **not** automatically close or resting orders cancel on pause. Resume *re-arms* only after account and order checks; the already-installed healthy Windows PAPER supervisor starts a new worker when allowed. Broker close requests are not evidence that fills completed. Test with PAPER and no open positions before attempting close operations; verify order status in Alpaca.

## Background services and supervised PAPER worker (Windows)

Opt-in `aion_paper_supervisor.py` can run `aion_alpaca_paper_algo.py run` independently of the browser and the dashboard terminal, under a per-user Windows Task Scheduler logon task created by `install_aion_paper_supervisor.ps1`. It starts a worker **only when already armed, account-bound, and not kill-switched**. A manual worker is never duplicated or silently taken over, and unexpected worker exits trigger the existing kill switch instead of restarting trading indefinitely. ARGUS displays supervisor status and can request the registered task to start when you press Resume.

**Warning:** If the PAPER bot is already armed at Windows sign-in, installing this task authorizes it to resume PAPER trading after you log in. It never automatically arms the bot, and it cannot manage orders while the PC is off, asleep or disconnected. Read [Windows supervisor setup and safeguards](README_AION_PAPER_SUPERVISOR.md) before registering or starting the task. Keep it PAPER-only.

### Current Windows background deployment

| Windows service / task | Expected normal state | Notes |
| --- | --- | --- |
| `AION-KAIROS-V5-Signal-Receiver` | Running | Port 8001 |
| `AION-TV-V5-Simulation-Gateway` | Running | Port 8002; public alert ingress via ngrok only |
| `AION-LTM-Dashboard` | Running | ARGUS website on port 8501 |
| `AION-Alpaca-Paper-Supervisor` | Running | Supervisor `RUNNING` **or** intentionally `WAITING`; worker health must be checked separately |
| `ngrok` **Windows service** | Running, startup Automatic | Standalone signed ngrok v3 executable; public TradingView tunnel |
| Legacy `AION-ngrok-TradingView` Task Scheduler task | Disabled | Replaced by Windows service; do not enable concurrently |
| Legacy `AION-ngrok-Watchdog` Task Scheduler task | Disabled | Replaced by Windows service; do not enable concurrently |

The ngrok Windows service was installed separately under Windows Service Control Manager after Task Scheduler restarts proved unreliable. A previous scheduled-task `Ready` state does **not** indicate a problem now that the service owns the tunnel. The four other long-lived AION processes use logon-triggered tasks under the signed-in Windows account. Closing PowerShell or the ARGUS browser tab does not intentionally stop them. A shut-down/sleeping computer, sign-out, network loss or unavailable provider can interrupt execution.

### Pre-market diagnostic (recommended 09:15 ET, weekdays)

```powershell
cd C:\Users\sathw\AION-Execution
Get-Service ngrok | Select-Object Name,Status,StartType
Get-ScheduledTask | Where-Object TaskName -Like 'AION-*' |
    Select-Object TaskName,State
.\.venv\Scripts\python.exe .\aion_paper_supervisor.py status
.\.venv\Scripts\python.exe .\aion_alpaca_paper_algo.py status
8001,8002,8501 | ForEach-Object {
    Get-NetTCPConnection -LocalPort $_ -State Listen -ErrorAction SilentlyContinue |
        Select-Object LocalPort,State
}
```

Confirm the worker **actually has a PID and a fresh heartbeat**, the current effective armed state is true, the kill switch is absent, the broker account is bound, and there are no unknown/open orders requiring reconciliation. `Supervisor: RUNNING` or Task Scheduler `Running` is **not** proof of an active or healthy trading worker. A `GET /tv-webhook` returning HTTP **405** is a *successful connectivity check* because actual TradingView alerts are JSON `POST` requests; neither 405 nor a TradingView HTTP **200** proves a broker fill.

**Fail-closed recovery:** On November 10 the PAPER worker encountered a transient connection error while reading Alpaca `/v2/account` overnight. It halted, activated `AION_PAPER_KILL_SWITCH.txt`, and had no fresh heartbeat during the morning's 10:03 ET KAIROS entry/10:07 ET exit; the TradingView webhooks were accepted but **Alpaca PAPER recorded no corresponding order**. The worker was manually reconciled and rearmed. Network restoration does **not** auto-clear the kill switch, and rearming establishes a new timestamp so old signals are not replayed. Safe bounded retries for **read-only** API calls and an explicit stale-heartbeat alert in ARGUS are **proposed improvements, not implemented features**. Never automatically retry an order with unknown submission outcome.


## Research/backtesting caveats

- Stock 3-minute history is available through imported bars/provider downloads; it is **not** a complete reconstruction of TradingView's Pine KAIROS V5 indicators.
- Options historical replay requires real bid/ask history (CSV or separately entitled data provider); connecting a paper brokerage does not populate that historical quote database.
- The free Alpaca indicative options feed is for development and forward testing, not reliable execution-quality performance estimates.
- See `README_AION_UNIFIED.md` and `docs/ARCHITECTURE.md` for limitations.

## Source layout

- **Root Python and scripts:** assembled current unified AION source.
- `aion_backtest_engine/`: integrated original v0.2 FastAPI engine.
- `backtest_bundle/`, `unified_parts/`, `research_components/`: legacy integration/upgrade build materials retained for reproducibility.
- `static/`: ARGUS-branded website with the original AION Quant Labs logo.
- `legacy/quant_terminal_v0_2_source/`: unmodified original independent Quant Terminal source.
- `legacy/synthetic_paper_demo/`: earlier fixed-price simulated paper broker demonstration.
- `legacy/snaptrade_alpaca_connection/`: earlier SnapTrade Paper connection helper, **not** the live worker.
- `patches/release_archives/`: historical patch ZIPs. Some are superseded; use the current root files rather than stacking patches blindly.
- `docs/`: architecture, Windows setup, security, and patch inventory.

## Security

**This GitHub repository is public.** Never commit `.env`, API secrets, TradingView webhook JSON containing the secret, SnapTrade credentials, brokerage account numbers, SQLite databases, fill journals, or logs. `.gitignore` blocks these categories, but always review `git status` before committing.

**Do not interpret a TradingView strategy alert as a brokerage fill.** The paper worker may have pending/partial/unknown orders; reconcile with Alpaca before making decisions. See `SECURITY.md`.

## Development status (updated November 10, 2026)

- **Implemented:** ARGUS desktop/mobile branding, Tailscale-only monitor access, authenticated PAPER controls, Windows background dashboard/signal/gateway tasks, an automatic-start ngrok Windows service, a separately supervised Alpaca PAPER worker, reconciled execution journal, integrated research/backtesting, and SPY 0DTE KAIROS V5 signal-to-paper execution.
- **Verification:** A PAPER SPY entry and exit were previously filled on November 6; broker account/position/order reconciliation has been exercised. Twenty offline PAPER safety tests were run successfully on the Windows Python 3.13 setup and the GitHub Actions Windows safety workflow passed. These facts do **not** constitute a guarantee of future fills, uptime or strategy profitability.
- **Known limitations:** An Alpaca read-only API timeout can stop the worker and trigger the kill switch; there is no automatic broker reconnection/rearming after such a halt. A signed-in and awake Windows computer, working Tailscale and outbound internet, and a live webhook tunnel are required for the respective functions. Missing or stale alerts are deliberately skipped. Indicative option quotes and historical bars are not guaranteed execution-quality data. Limit exits and emergency cutoffs cannot ensure liquidation. A daily loss cap has **not** been implemented.
- **Not deployed:** SnapTrade/Webull live routing, any real-money broker order handler, independently approved live trading controls, and the proposed resilience/heartbeat-warning enhancements. Existing archived installers and diagrams may describe older Cloudflare/terminal-based experiments; the operational setup above describes the current reported Windows configuration.

### November 5, 2026 — Alpaca options-chart preflight

If historical SPY options bars return `Alpaca DATA HTTP 400`, use the **read-only** [`aion_option_preflight.py`](aion_option_preflight.py) and the [preflight guide](README_AION_PAPER_PREFLIGHT.md) to diagnose the historical quote entitlement and GET parameters without arming the paper bot. The ZIP is archived in [`patches/release_archives/`](patches/release_archives/). **No new brokerage orders** are placed by this diagnostic.

### November 5, 2026 — historical SPY options bars HTTP 400 repair

The Alpaca historical option-bars API rejects `feed=indicative` (`unexpected query parameter(s): feed`); the historical bar loader and chart preflight now omit that parameter, cap requests conservatively for a Basic-plan delay, and label historical *trade bars* separately from indicative latest quotes. See [chart fix and installation](README_AION_OPTIONS_BARS_FEED_FIX.md). The **$2,500 total premium** KAIROS V5 paper-order logic and account guards are unchanged; this release was tested **offline** and still requires a successful Alpaca call on the Windows host. At the time of that historical release, PAPER automation was kept **disarmed** pending verification; this does not describe its current dynamic state.

## License

This repository is covered by its existing [MIT License](LICENSE), except where separate third-party terms apply.
