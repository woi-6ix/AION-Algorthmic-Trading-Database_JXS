# AION Live Trading Monitor — stable, per-Windows-user PAPER control token.
# No Alpaca keys or broker data are written by this script.
# Run this in PowerShell instead of invoking uvicorn directly.
$ErrorActionPreference = 'Stop'

$root = $PSScriptRoot
if (-not $root) { throw 'Cannot determine script directory.' }
$python = Join-Path $root '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "Missing Python virtual environment: $python"
}
if (-not (Test-Path -LiteralPath (Join-Path $root 'aion_ltm_with_controls.py') -PathType Leaf)) {
    throw 'Missing aion_ltm_with_controls.py. Install the AION LTM PAPER controls first.'
}

# Do not stop a process automatically: another dashboard/Windows startup task may own port 8501.
$listener = @(Get-NetTCPConnection -LocalPort 8501 -State Listen -ErrorAction SilentlyContinue)
if ($listener.Count -gt 0) {
    $listenerPid = $listener[0].OwningProcess
    $proc = Get-CimInstance Win32_Process -Filter "ProcessId = $listenerPid" -ErrorAction SilentlyContinue
    Write-Warning "Port 8501 is already owned by PID $listenerPid. Stop only the old dashboard before starting this one."
    if ($proc) { Write-Host ("Process: " + $proc.Name + " (PID " + $listenerPid + ")") }
    throw 'Dashboard not started: port 8501 is already in use. Do not regenerate your token.'
}

# One stable token for this Windows account, stored outside the Git repository.
if (-not $env:LOCALAPPDATA) { throw 'LOCALAPPDATA is unavailable.' }
$tokenDir = Join-Path $env:LOCALAPPDATA 'AION'
$tokenFile = Join-Path $tokenDir 'ltm-control-token.txt'
if (-not (Test-Path -LiteralPath $tokenDir -PathType Container)) {
    New-Item -ItemType Directory -Path $tokenDir -Force | Out-Null
}
if (-not (Test-Path -LiteralPath $tokenFile -PathType Leaf)) {
    $bytes = New-Object byte[] 48
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
    $generated = [Convert]::ToBase64String($bytes).TrimEnd('=').Replace('+', '-').Replace('/', '_')
    [System.IO.File]::WriteAllText($tokenFile, $generated, [System.Text.Encoding]::ASCII)
    Write-Host 'Created a new persistent AION LTM control token in your private Windows profile.'
}

$token = [System.IO.File]::ReadAllText($tokenFile, [System.Text.Encoding]::ASCII).Trim()
if ($token.Length -lt 48 -or $token -notmatch '^[A-Za-z0-9_-]+$') {
    throw 'Existing AION control token file is invalid; leaving it unchanged. Review the file locally.'
}

# Child process receives exactly the same token copied to the browser.
$env:AION_CONTROL_TOKEN = $token
try {
    Set-Clipboard -Value $token -ErrorAction Stop
    Write-Host 'AION control token copied to clipboard. Paste it into the browser Unlock field.'
} catch {
    Write-Warning 'Clipboard unavailable. Copy the token locally with: Get-Content "$env:LOCALAPPDATA\AION\ltm-control-token.txt" -Raw | Set-Clipboard'
}
Write-Host 'Starting AION LTM with the same token on each restart (Alpaca PAPER only).'
Set-Location -LiteralPath $root
& $python -m uvicorn aion_ltm_with_controls:app --host 127.0.0.1 --port 8501