# AION PAPER worker — supervised background operation (Windows)

The existing Alpaca **PAPER-only** bot is an independent process. Browser reloads and dashboard restarts should not affect it. The new Windows Task Scheduler task launches `aion_paper_supervisor.py` in the logged-in Windows user's own session. AION LTM still runs separately on port 8501. **This does not deploy to the user's Windows PC automatically.**

## Critical safety properties

- The supervisor **never arms, disarms or places orders**. It only runs the existing `aion_alpaca_paper_algo.py run` when the worker's SQLite state is already **armed**, the account is bound, and its kill switch is absent. The underlying paper worker retains its hard-coded `https://paper-api.alpaca.markets` endpoint, position limits and account guards.
- **If ARMED at Windows sign-in, automation can automatically resume trading after sign-in**, even when the browser is closed. Install/enable the task only if this is what you intend. Task Scheduler uses `Interactive` logon: the Windows user must sign in; it does not run before logon or after sign-out.
- Only one supervisor holds the OS supervisor lock; the existing worker OS lock prevents multiple trading workers. If a manual trading worker already holds the lock, the supervisor does not interfere or silently take over after it stops. Pause/Resume deliberately to hand off.
- **Pause** disarms the worker and eventually stops its trading loop; the supervisor remains running but idle. **Resume** re-arms only after all existing worker account/order guards pass; a healthy supervisor then launches the worker. If the supervisor task is offline, the dashboard attempts to start the installed task and reports its state. Neither action cancels resting orders or closes positions.
- An unexpected worker exit, failed worker launch, or supervisor failure activates `AION_PAPER_KILL_SWITCH.txt` and **does not auto-restart trading**. Inspect the Alpaca PAPER account, positions, orders and local logs; only re-arm after reconciliation. The supervisor never bypasses this kill switch.
- This does **not** ensure market-data freshness, fill success, no in-flight orders, unattended 0DTE safety, internet availability, sleep prevention, or re-entry protection beyond the existing worker. Keep initial PAPER tests supervised.

## Install on your existing Windows PC

1. Review the PAPER account and open orders. Prefer installing while there are **zero open positions/orders** and the worker is disarmed. Installing when ARMED is possible but **starting** the task can immediately enable PAPER trading. If your manually started worker is running, stop it deliberately at an appropriate time, and do not stop it with Task Manager during an in-flight order.
2. In `C:\Users\sathw\AION-Execution` download only these four source files from the AION GitHub `main` branch, backing up existing versions first: `aion_paper_supervisor.py`, `install_aion_paper_supervisor.ps1`, `aion_alpaca_control.py`, `aion_alpaca_controls.js`. Preserve `.env`, `.sqlite3`, and the existing worker and gateway. The supervisor **does not require editing the worker**.
3. Open Windows PowerShell **as the same Windows user who runs AION**. If necessary for this trusted downloaded script, use `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force` (only in that PowerShell window).
4. Register the logon task, but **do not start trading yet**:

```powershell
cd C:\Users\sathw\AION-Execution
.\install_aion_paper_supervisor.ps1
Get-ScheduledTask -TaskName AION-Alpaca-PAPER-Supervisor | Select-Object TaskName,State
```

5. When you intentionally want the background supervisor running (and understand the bot may be ARMED), start the scheduled task:

```powershell
Start-ScheduledTask -TaskName AION-Alpaca-PAPER-Supervisor
Start-Sleep -Seconds 5
.\.venv\Scripts\python.exe .\aion_paper_supervisor.py status
```

6. Restart only the **AION dashboard** with your existing persistent token launcher (`.\start_aion_ltm_controls.ps1`). The controls now show the supervisor status alongside the worker heartbeat. The dashboard **does not need to remain open** for the task to operate.

## Troubleshooting and operating commands

```powershell
# Supervised worker status (local read-only)
.\.venv\Scripts\python.exe .\aion_paper_supervisor.py status
# Saved bot arm/kill status (local read-only)
.\.venv\Scripts\python.exe .\aion_alpaca_paper_algo.py status
# Scheduled task status
Get-ScheduledTask -TaskName AION-Alpaca-PAPER-Supervisor | Select TaskName,State
# Inspect local logs. May include trading diagnostics; keep private.
Get-Content .\logs\aion-paper-supervisor.log -Tail 30
Get-Content .\logs\aion-paper-worker-supervised.log -Tail 40
```

To stop further bot processing, use **Pause automation** from the dashboard or `python aion_alpaca_paper_algo.py disarm`. To stop the supervisor process itself (e.g., for upgrades) after disarming and checking orders:

```powershell
Stop-ScheduledTask -TaskName AION-Alpaca-PAPER-Supervisor
```

**Warning:** Force-stopping the scheduled task while a child worker is active may leave its worker process running separately. Verify its status and inspect broker orders rather than assuming the account is safe.

To remove the *logon registration* (without changing your broker or SQLite state):

```powershell
Stop-ScheduledTask -TaskName AION-Alpaca-PAPER-Supervisor
Unregister-ScheduledTask -TaskName AION-Alpaca-PAPER-Supervisor -Confirm:$false
```

## Operational interpretation

- `RUNNING` — supervisor child is alive (a **recent worker heartbeat** is still the execution-health indicator).
- `STARTING` — child launched; wait for a successful heartbeat.
- `WAITING` — supervisor idle because bot is disarmed, kill-switched, or unbound.
- `EXTERNAL_WORKER` — another/manual worker holds the worker lock; no duplicate started.
- `WAITING_OPERATOR` — manual worker stopped; operator must Pause/Resume for an explicit handoff.
- `HALTED` — investigate a crash/launch error and kill switch. Do not auto-rearm.
- `OFFLINE` — Task Scheduler task isn't running or heartbeat file is stale.

The status file, lock files, and logs are machine-local, not source files. Never push them to GitHub.