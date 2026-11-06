AION LTM: ALPACA PAPER CONTROLS — MANUAL INSTALL
================================================

These files ADD a control panel to your existing AION Live Trading Monitor.
They do not overwrite aion_live_dashboard.py or your current static/index.html.

IMPORTANT: The existing GitHub version of aion_alpaca_paper_algo.py needs a
small, secure non-interactive arm authorization update for the RESUME button.
Run the included install_ltm_worker_arm_patch.py AFTER BACKING UP your worker.
It modifies only the exact matching arm guard and creates a timestamped backup.
This does not automatically start a stopped bot process.

Included:
 - aion_ltm_with_controls.py  -> launch wrapper, preserves original monitor
 - aion_alpaca_control.py     -> authenticated PAPER broker API + worker CLI
 - aion_alpaca_controls.js   -> floating control panel on existing website
 - install_ltm_worker_arm_patch.py -> adds authenticated dashboard arm guard

1. STOP existing dashboard process on port 8501 (Ctrl+C).

2. Copy the Python/JavaScript files from the ZIP next to your existing:
   C:\Users\sathw\AION-Execution\aion_live_dashboard.py
   C:\Users\sathw\AION-Execution\aion_alpaca_paper_algo.py
   Keep all existing static assets / .env / SQLite DBs unchanged.

3. In PowerShell, apply the secure dashboard-arm compatibility patch
   (the installer first makes a backup; if your local worker differs it refuses):

   .\.venv\Scripts\python.exe .\install_ltm_worker_arm_patch.py

4. In PowerShell (the SAME terminal that will launch the dashboard):

   cd C:\Users\sathw\AION-Execution
   $env:AION_CONTROL_TOKEN = -join ((1..48) | ForEach-Object { 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789'[Get-Random -Maximum 62] })
   $env:AION_CONTROL_TOKEN  # display privately ONCE; paste into dashboard control panel

   Never put this token or Alpaca API keys into browser JS, GitHub, or screenshots.
   The token resets if you start a fresh terminal, unless you set it again.

5. Ensure Alpaca PAPER API credentials already exist in your private .env or
   server environment; use the same PAPER credentials as your worker.
   Accepted pairs include APCA_API_KEY_ID / APCA_API_SECRET_KEY.

6. Launch:

   .\.venv\Scripts\python.exe -m uvicorn aion_ltm_with_controls:app --host 127.0.0.1 --port 8501

   IMPORTANT: launch aion_ltm_with_controls:app (not aion_live_dashboard:app).

7. Open your existing LTM URL (127.0.0.1:8501 or private Tailscale URL).
   A new floating "ALPACA PAPER CONTROLS" button appears at bottom right.
   Unlock with AION_CONTROL_TOKEN; press Refresh to check worker + broker.

8. Dry-run safely:
   a) Test Refresh and PAUSE while NO paper positions are open.
   b) Check worker status in another shell:
      .\.venv\Scripts\python.exe .\aion_alpaca_paper_algo.py status
   c) Test RESUME only when you intend to allow new PAPER trades.
   d) Open/close a tiny PAPER test position only if you understand the order risks.

Notes:
 - ONLY https://paper-api.alpaca.markets is used (hardcoded).
 - PAUSE calls your worker's existing "disarm" CLI command. RESUME calls "arm".
   Your existing worker must support the confirmation prompt "ARM ALPACA PAPER".
 - ARM does not launch a worker. 'Armed (worker not confirmed)' means heartbeat stale.
 - PAUSE may also prevent automatic EXIT orders depending on worker implementation.
 - Manual close first disarms the worker, then submits a broker close order.
 - CLOSE ALL is account-wide on Alpaca PAPER and cancels all open orders.
 - Broker order acceptance is NOT confirmation the position is flat; verify fills.
 - Dashboard controls cannot guarantee there are no in-flight worker orders.
 - The control token is held in page memory only and is lost on refresh.
 - Your current Tailnet-only Tailscale Serve setup should remain in place.
   Do not expose port 8501 to the public Internet without HTTPS + extra access controls.
 - If status schema or arm/disarm CLI changed locally, requests fail closed.
 - Test against Alpaca PAPER; this package does NOT support live trading.