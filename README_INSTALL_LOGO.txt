AION Quant Labs — Official Logo Update
=====================================
Adds the existing gold/navy AION emblem to both Windows and iPhone dashboard
headers, plus browser tab icon. Does not change signal processing, trading
services, your .env, or any account connection.

1. Save AION_Official_Logo_Dashboard_Patch.zip to your Downloads folder.
2. Open PowerShell:

   cd C:\Users\sathw\AION-Execution
   Expand-Archive -Path "$env:USERPROFILE\Downloads\AION_Official_Logo_Dashboard_Patch.zip" -DestinationPath . -Force
   .\.venv\Scripts\python.exe .\apply_aion_logo.py

3. Restart ONLY the dashboard service (port 8501). Stop its existing process
   using Ctrl+C in that dashboard terminal, then run:

   .\.venv\Scripts\python.exe -m uvicorn aion_live_dashboard:app --host 127.0.0.1 --port 8501

4. Refresh: https://jxs-hpx360.tailf4db21.ts.net/ on iPhone with Tailscale VPN enabled.

The installer makes timestamped backups of the existing Python and HTML files.
Leave AION server (8001), webhook gateway (8002) and Tailscale Serve unchanged.
