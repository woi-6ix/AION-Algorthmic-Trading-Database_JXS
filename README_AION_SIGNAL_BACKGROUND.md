# AION KAIROS V5 signal receiver and TradingView gateway: Windows background tasks

This is **separate** from `AION-LTM-Dashboard` (8501) and `AION-Alpaca-PAPER-Supervisor`. It runs two **SIMULATION-only** components as per-user Task Scheduler tasks:

| Task | Port | Implementation |
|---|---|---|
| `AION-KAIROS-V5-Signal-Receiver` | `127.0.0.1:8001` | `aion_signal_background.py receiver` launches `aion_kairos_server:app` |
| `AION-TV-V5-Simulation-Gateway` | `127.0.0.1:8002` | `aion_signal_background.py gateway` launches `aion_tv_gateway:app` |

These components **never arm the PAPER bot, submit Alpaca orders, or clear a kill switch**. The gateway forwards valid authenticated KAIROS V5 messages to the local signal receiver. **The separately armed Alpaca PAPER worker may later consume those intents and submit orders**, so leave it disarmed while testing. Do not auto-start a public Cloudflare Quick Tunnel; it exposes the authenticated webhook endpoint and its hostname changes across restarts.

## Setup (Windows PowerShell)

1. Back up any same-named files before replacing. Put `aion_signal_background.py` and `aion_signal_services.ps1` beside the existing `aion_kairos_server.py` and `aion_tv_gateway.py` in `C:\Users\sathw\AION-Execution`.
2. Keep your existing `.env` and `.venv` unchanged. Verify locally that `.env` has `AION_MODE=SIMULATION`, `AION_ALLOWED_STRATEGY=KAIROS_V5`, and a strong dedicated `AION_TV_WEBHOOK_SECRET` (32+ characters). The manager checks those fields without printing values. If the secret is missing, use the existing `configure_tv_secret.py` **locally** and then update your TradingView alert secret deliberately; do not print/share the secret or `.env`.
3. Install and start the two tasks:

```powershell
cd "$env:USERPROFILE\AION-Execution"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\aion_signal_services.ps1 -Action Install
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\aion_signal_services.ps1 -Action Status
```

4. If port 8001 or 8002 is already occupied, the installer does **not** stop or replace an unknown process. Identify the listener first, stop an old *manual* service gracefully, then use `-Action Start` to hand off.
5. For diagnostics:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\aion_signal_services.ps1 -Action Logs
```

Expected `Status`: both tasks `Running`; receiver health reports `SIMULATION` with `broker_orders_enabled: false`; gateway GET `/tv-webhook` returns **405**, which is correct because it accepts POST only.

## End-to-end simulation check

Once both services are healthy **and the PAPER worker remains disarmed**, the existing `test_tv_gateway.ps1` sends a test authenticated TradingView-style *simulation intent* through gateway → receiver. This writes to the simulator's SQLite state and **does not submit brokerage orders**:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\test_tv_gateway.ps1
```

Avoid this POST test if the PAPER worker is armed, because it could consume that intent. The test changes simulation state, so review/reset **simulation intent only** with the existing `reset_simulation_state.py` when appropriate. Do not reset any broker, worker or trade journal DB.

## Operating limitations

- The tasks auto-start **when the current Windows user signs in**, not before login; they stop if the computer shuts down and do not run while the PC sleeps. They restart up to 3 times after a process failure. They never auto-arm the PAPER worker.
- Only the local loopback ports are exposed. A TradingView alert on the internet **cannot reach `127.0.0.1:8002`** without a separate trusted HTTPS ingress/tunnel. Current `cloudflared` startup is not modified, and no public URL is configured.
- The original gateway is **simulation-only** and must not be represented as production-grade origin-authenticated or reliable delivery. The current receiver may have session checking disabled for local tests. Keep PAPER trading disarmed until webhook origin, session rules, signal staleness, reconciliation, and exit handling have been reviewed under supervised test conditions.
- Stopping tasks does not stop manually launched servers, change TradingView alerts, clear state, pause/resume AION or cancel any broker order.
- Never share `.env`, token files, TradingView secret or logs containing unexpected credentials.

Stop just these services:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\aion_signal_services.ps1 -Action Stop
```

Unregister them (does not modify LTM or PAPER supervisor):

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\aion_signal_services.ps1 -Action Uninstall
```
