# AION LTM + Alpaca PAPER — Independent background tasks (Windows)

Runs two independent **per-user Windows Task Scheduler processes** without needing to keep PowerShell or the browser open:

| Windows task | Program | Purpose |
|---|---|---|
| `AION-LTM-Dashboard` | `.venv\Scripts\pythonw.exe aion_ltm_background.py` | Serves existing control-enabled LTM locally at `http://127.0.0.1:8501` using the existing persistent control token. The task is set to restart up to 3 times following a failure. |
| `AION-Alpaca-PAPER-Supervisor` | `.venv\Scripts\pythonw.exe aion_paper_supervisor.py run` | Independently supervises `aion_alpaca_paper_algo.py run` and respects the existing arm state and kill switch. **No automatic worker restart or re-arm after unexpected failure.** |

**Limits:** Task Scheduler's `Interactive` logon type starts tasks **after this Windows user signs in**. They cannot run before login or after sign-out, when the PC is shut down/asleep, or when connectivity is unavailable. Keep the Windows user signed in, the PC awake, and Tailscale running for remote Tailnet access. The current Tailnet-only proxy to `127.0.0.1:8501` does not need to be changed. Do not expose the control endpoint to the public internet.

## Files

Add `aion_ltm_background.py` and `aion_services.ps1` beside the existing `aion_ltm_with_controls.py`, `aion_alpaca_paper_algo.py` and `aion_paper_supervisor.py` in `C:\Users\sathw\AION-Execution`. Keep your existing `.env`, SQLite databases, logs, token and kill-switch file **untouched**. Use your project's existing `.venv`.

## Safe initial setup

1. Before changing the worker, inspect paper positions, resting orders, the local kill switch, and confirm your earlier worker-lock test passes. **Do not re-arm the worker if the lock test or broker reconciliation fails.** Prefer zero open orders and positions.
2. Stop any manually launched **LTM dashboard** on port 8501 using its original terminal / Ctrl+C (do not indiscriminately kill processes). Existing manual worker is not touched by dashboard setup.
3. From **Windows PowerShell as your normal Windows account**:

```powershell
cd C:\Users\sathw\AION-Execution
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\aion_services.ps1 -Action Install
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\aion_services.ps1 -Action Status
```

The installation starts the dashboard **if port 8501 is free** and schedules it for sign-in. For a **new** worker task it registers the supervisor **disabled**, so it cannot start trading just because the PC reboots. A pre-existing supervisor task is preserved and must be reviewed separately (the installer does not overwrite or disable an already running task). Neither task's installer arms the worker, submits trades, changes `.env`, or clears the kill switch.

4. If dashboard setup encountered port 8501 already occupied, inspect and stop the original dashboard safely, then:

```powershell
.\aion_services.ps1 -Action StartDashboard
.\aion_services.ps1 -Action CopyToken
```

Open `http://127.0.0.1:8501` (or the existing Tailnet-only Tailscale URL). Paste the token copied by `CopyToken` into the LTM unlock field. This is the same token created by `start_aion_ltm_controls.ps1`, **not a new token**.

5. **Only after** the PAPER account, open orders, worker-lock test, arm/kill state, and local service logs are verified, explicitly enable and start the supervisor:

```powershell
.\aion_services.ps1 -Action EnableWorker -ConfirmWorkerActivation
.\aion_services.ps1 -Action StartWorker -ConfirmWorkerActivation
Start-Sleep -Seconds 5
.\aion_services.ps1 -Action Status
```

`EnableWorker` allows the supervisor to launch at future sign-ins, and `StartWorker` launches it now. **If the worker is already ARMED and no kill switch is present, it can immediately start placing PAPER orders.** If disarmed or kill-switched, the supervisor stays idle; do not bypass safety state. A manual worker holding the execution lock is not duplicated. The supervisor deliberately fails closed after a crash; investigate the broker and worker logs before a deliberate re-arm.

## Operational commands

```powershell
.\aion_services.ps1 -Action Status       # Tasks, dashboard HTTP, live processes, worker heartbeat
.\aion_services.ps1 -Action Logs         # Last 20 lines of each local service log
.\aion_services.ps1 -Action CopyToken    # Recopy existing token privately; no regeneration
.\aion_services.ps1 -Action StopDashboard
.\aion_services.ps1 -Action StartDashboard
.\aion_services.ps1 -Action DisableWorker # Disable FUTURE sign-in runs, not current worker
```

**Pause automation in LTM before stopping the supervisor.** Stopping a running worker while a paper option is open may prevent exit management; neither Pause nor stopping the service closes positions or cancels resting orders. After checking broker state, disarming, and waiting for the worker process to exit:

```powershell
.\aion_services.ps1 -Action StopWorker
# Optional, removes task registrations only after the worker is no longer armed:
.\aion_services.ps1 -Action Uninstall
```

The `Status` action distinguishes (a) an armed permission flag, (b) a fresh worker heartbeat, (c) live Windows worker processes, and (d) a fresh supervisor status. **ARMED alone does not mean automation is actually executing.** A fresh heartbeat is still not a guarantee that orders are executing correctly; check Alpaca PAPER directly.

## Troubleshooting

- **Dashboard task Running but site not responding:** inspect `logs\aion-ltm-background.log`, port 8501 and the task's `LastTaskResult`.
- **Dashboard won't start:** another process may own port 8501. `Get-NetTCPConnection -LocalPort 8501 -State Listen | Select-Object LocalAddress,OwningProcess` to identify it; don't kill it blindly.
- **Worker supervisor WAITING:** the bot is disarmed, kill-switch is set, or the account is not bound.
- **Worker supervisor HALTED / worker heartbeat stale / multiple worker processes:** no automatic restart. Inspect local `logs\aion-paper-supervisor.log` and `logs\aion-paper-worker-supervised.log`, run the lock test, and reconcile PAPER orders and positions before re-arming.
- **No dashboard when the PC is signed out:** this is expected for interactive logon tasks; they are not Windows system services.
- **Tailscale URL doesn't open:** confirm the existing `tailscale serve status` points to `127.0.0.1:8501` and that Tailscale is connected; service setup does not configure external ingress.

**PAPER ONLY:** No live-trading broker endpoint is configured by these files. Windows production execution and dashboard/worker integration should be supervised and validated with the existing KAIROS test environment before relying on unattended operation.