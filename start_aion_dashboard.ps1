$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
Write-Host 'AION Live Monitor - read-only dashboard on http://127.0.0.1:8501'
if (!(Test-Path '.\.venv\Scripts\python.exe')) { throw 'Missing .venv - extract this package into AION-Execution' }
& '.\.venv\Scripts\python.exe' -m uvicorn aion_live_dashboard:app --host 127.0.0.1 --port 8501
