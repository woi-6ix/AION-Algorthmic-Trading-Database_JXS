# AION Quant Labs — Live Trading Monitor (read-only, mobile-ready)

## What this add-on does

This is a **separate read-only monitoring website**, designed to complement the existing AION Quant Terminal without overwriting it.

- Reads your existing KAIROS V5 simulator's `GET /health`, `/status`, `/events?limit=50` at `http://127.0.0.1:8001`.
- Shows signals, alerts, repeated/blocked signals, current directional intent and service status.
- Shows **paper or live option fills only after an execution/reconciliation component writes a confirmed fill** to the local `aion_execution_journal.sqlite3` using `record_fill()` in `aion_execution_journal.py`. No trading / order-placement code exists in this package.
- Mobile-friendly dark interface, refreshed every eight seconds.
- Does **not** read `.env`, use SnapTrade credentials, submit orders, or expose any write APIs.

IMPORTANT: You do not yet have broker executions. Your existing five signals are simulator tests; they will display as signals, **not trades**. AION's fill table begins empty.

## 1 — Install on Windows

Download/extract the ZIP into `C:\Users\sathw\AION-Execution` (same directory as `.venv` and `aion_kairos_server.py`). This ZIP won't overwrite the KAIROS service, webhook gateway, `.env`, or SQLite signal history.

In PowerShell:

```powershell
cd C:\Users\sathw\AION-Execution
Expand-Archive "$env:USERPROFILE\Downloads\AION_Live_Mobile_Trading_Dashboard.zip" -DestinationPath . -Force
.\.venv\Scripts\python.exe -m pip install -r .\requirements-dashboard.txt
.\.venv\Scripts\python.exe -m uvicorn aion_live_dashboard:app --host 127.0.0.1 --port 8501
```

Leave this terminal open. Open `http://127.0.0.1:8501` on your PC.

Leave your existing services running:
- Existing AION Quant Terminal (typically `127.0.0.1:8000`, separate project)
- KAIROS V5 simulator: `127.0.0.1:8001`
- TradingView webhook gateway: `127.0.0.1:8002`
- Cloudflare Quick Tunnel for TradingView only (your `/tv-webhook` address)
- Live Monitor: `127.0.0.1:8501`

## 2 — Private access from your phone or elsewhere with Tailscale

Install Tailscale on the Windows PC and phone, log in using the same Tailscale account / authorized tailnet, and turn it on for both. See `https://tailscale.com/download`.

In Windows PowerShell after installation:

```powershell
tailscale status
tailscale serve --bg http://127.0.0.1:8501
tailscale serve status
```

`tailscale serve status` should print an HTTPS address ending in `.ts.net`. Open **that exact URL** in your phone browser, with Tailscale connected on your phone. You don't need a domain or public hosting.

**Tailscale Personal free is limited to non-commercial usage**. AION Quant Labs is a startup; review Tailscale's business pricing/terms before using the Personal plan as a business service. You can still test locally for free.

The Windows PC must be **powered on, connected and running the services** to view new data remotely. The dashboard is not hosted in the cloud. Use Tailscale Serve (`tailscale serve`), **not Funnel**, which would make the app public.

Do NOT point your existing public Cloudflare Quick Tunnel at this dashboard; that's for authenticated TradingView ingestion only.

## 3 — Future paper/live fill integration

When a **separate executor** has independently confirmed a fill (from a permitted paper engine or from SnapTrade broker order reconciliation), call:

```python
from aion_execution_journal import record_fill

record_fill(
  fill_id="external-unique-fill-id",           # broker execution/fill ID
  filled_at="2026-11-05T15:04:12Z",            # actual confirmed fill time, UTC
  option_symbol="SPY   261105C00767000",   # verified OCC symbol
  mode="PAPER",                               # or LIVE only for broker-confirmed fills
  action="BUY_TO_OPEN",                        # or SELL_TO_CLOSE
  contracts=1,
  fill_price=0.89,
  fees_usd=0.00,
  source="verified-paper-engine",
)
```

**This code is an integration example, not a command to run today.** Never create fake LIVE fills. `record_fill` is idempotent by fill ID. AION reads this journal automatically every eight seconds; no restart necessary. P&L is calculated from matching fill records, using 100x option multiplier, minus recorded entry/exit fees. It does not include FX, unmatched inventory, unrealized P&L, or live market marks. With over 500 fills, historical P&L can be incomplete.

The read-only dashboard does not itself send trades to Webull or SnapTrade. The previously built SnapTrade preflight also never submits orders.

## 4 — Existing AION Quant Terminal

Your earlier dashboard is a separate FastAPI project (`aion-quant-terminal`) often running on port 8000. This add-on works now, without modifying its source. If you want the new trading monitor **embedded into the original Quant Terminal's navigation and layout**, its Python frontend/route code needs to be updated in that project. This package does not pretend to have modified a local folder that isn't available here.

## Optional: embed inside your existing FastAPI terminal later

For a FastAPI main app whose Python can import `aion_live_dashboard`, add after its `app = FastAPI(...)` setup:

```python
from aion_live_dashboard import app as aion_live_monitor
app.mount('/live', aion_live_monitor)
```

Then browse `/live/` on the same host. This requires placing the new modules and static file where the existing AION terminal process can import/read them; it is **not automatically applied** by this package. The independent port-8501 deployment is the tested default.

## Troubleshooting

- Blank/no data: run `Invoke-RestMethod http://127.0.0.1:8001/status` and verify KAIROS server is running. The monitor will show `AION OFFLINE` if it cannot connect.
- The `.ts.net` page can't load: check `tailscale status`, confirm both phone and PC are connected, and rerun `tailscale serve status`.
- Port 8501 already in use: run the monitor on 8502 and update the corresponding Tailscale Serve target.
- Don't commit `.env`, `.sqlite3` or database files to a public repo. Add `*.sqlite3` and `*.sqlite3-*` to your existing `.gitignore`.

**Security note:** The monitor does not implement its own password login. Access protection is provided by your private Tailscale network. Anyone you authorize to that tailnet/device may be able to view the dashboard. Never publish it openly without adding authentication and authorization.
