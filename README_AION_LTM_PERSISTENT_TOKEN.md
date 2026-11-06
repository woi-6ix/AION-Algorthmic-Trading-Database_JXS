# AION LTM — fix repeated `Invalid control token`

## Why the token was rejected

`aion_alpaca_control.py` reads `AION_CONTROL_TOKEN` from the **running dashboard server's environment**. Generating a new token in a different PowerShell window does not change that running process. Previously, browser refreshes also clear the token stored only in browser memory.

## Fix: use one persistent startup script

The `start_aion_ltm_controls.ps1` script creates a cryptographically random token *once* in your own Windows account's `%LOCALAPPDATA%\AION\ltm-control-token.txt`. This file stays outside the Git repository, ZIPs, and `.env` and is not displayed in the terminal. Each launch loads that same token and copies it to your Windows clipboard. The script refuses to start if port 8501 is already occupied and does not terminate unrelated processes.

1. Put `start_aion_ltm_controls.ps1` next to `aion_ltm_with_controls.py` in `C:\Users\sathw\AION-Execution`.
2. Stop the **dashboard** running on port 8501 (Ctrl+C in its PowerShell window). Leave your Alpaca paper worker and simulator running.
3. In PowerShell:

   ```powershell
   cd C:\Users\sathw\AION-Execution
   .\start_aion_ltm_controls.ps1
   ```

   If Windows blocks running the script under your policy, run it for this process without changing system-wide settings:

   ```powershell
   powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\start_aion_ltm_controls.ps1
   ```

4. Open `http://127.0.0.1:8501` (or your private Tailnet URL), click `ALPACA PAPER CONTROLS`, and paste **Ctrl+V**, then Unlock.
5. If you refreshed the browser, reopen a separate PowerShell window and recopy the *same* stable token (do not regenerate it):

   ```powershell
   Get-Content "$env:LOCALAPPDATA\AION\ltm-control-token.txt" -Raw | Set-Clipboard
   ```

6. If you use a Windows scheduled task or startup script for port 8501, update it to launch **this PowerShell script** rather than `uvicorn aion_live_dashboard:app` or a second `uvicorn aion_ltm_with_controls:app` instance. Use only one dashboard server listening on 8501.

### Optional safe token check (does not print your secret)

Run in another PowerShell window after launching the dashboard:

```powershell
$token = (Get-Content "$env:LOCALAPPDATA\AION\ltm-control-token.txt" -Raw).Trim()
try {
  $response = Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:8501/api/alpaca-control/state' -Headers @{'X-Aion-Control-Token'=$token}
  Write-Host "Token accepted (HTTP $($response.StatusCode))."
} catch {
  $code = [int]$_.Exception.Response.StatusCode
  if ($code -eq 401) { Write-Host 'Token mismatch: a different server/token is running on port 8501.' }
  elseif ($code -eq 503) { Write-Host 'Token accepted; downstream worker or broker unavailable (HTTP 503).' }
  else { Write-Host "HTTP $code. Inspect the dashboard server logs without sharing secrets." }
}
```

Do not paste the token, `.env`, or brokerage API keys into ChatGPT, GitHub, or screenshots. Protect your Windows account and restrict remote access to your Tailnet. This changes only dashboard authentication startup; it **does not arm, disarm, or trade** on Alpaca. Avoid restarting the worker if it is actively managing positions without reviewing its status first.