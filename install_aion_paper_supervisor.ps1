# AION Alpaca PAPER worker supervisor — per-user Windows Task Scheduler registration.
# Registration is inert until task starts. It NEVER arms/disarms the bot or closes orders.
# Usage: .\install_aion_paper_supervisor.ps1
#        .\install_aion_paper_supervisor.ps1 -StartNow  # enables worker if already ARMED!
param([switch]$StartNow)
$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$pythonw = Join-Path $root '.venv\Scripts\pythonw.exe'
$supervisor = Join-Path $root 'aion_paper_supervisor.py'
$worker = Join-Path $root 'aion_alpaca_paper_algo.py'
$taskName = 'AION-Alpaca-PAPER-Supervisor'
foreach ($path in @($pythonw, $supervisor, $worker)) {
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        throw "Missing required file: $path"
    }
}
if (-not (Get-Command Register-ScheduledTask -ErrorAction SilentlyContinue)) {
    throw 'Requires Windows Task Scheduler PowerShell cmdlets; use Windows PowerShell 5.1.'
}
$user = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$action = New-ScheduledTaskAction -Execute $pythonw -Argument ('"' + $supervisor + '" run') -WorkingDirectory $root
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $user
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Seconds 0) -RestartCount 0 -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName $taskName -Description 'AION Paper-only supervised trading worker; never automatically arms a bot. Fail-closed on errors.' -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null
Write-Host "Installed $taskName for $user. Starts at your next Windows logon."
Write-Host 'IMPORTANT: If the PAPER worker is ARMED at Windows logon, it may process trades automatically after sign-in.'
Write-Host 'The supervisor itself does NOT arm the bot. It respects the existing kill switch.'
if ($StartNow) {
    $manual = @(Get-CimInstance Win32_Process | Where-Object {
        $_.CommandLine -match 'aion_alpaca_paper_algo\.py\s+run'
    })
    if ($manual.Count -gt 0) {
        Write-Warning 'A manual Alpaca PAPER worker appears to be running. To avoid an unsafe handoff, stop it when appropriate, then explicitly Pause/Resume before supervising trades.'
    }
    Write-Host 'Starting supervisor now. If already armed and no manual worker exists, PAPER trades can be enabled immediately.'
    Start-ScheduledTask -TaskName $taskName
    Start-Sleep -Seconds 3
    Get-ScheduledTask -TaskName $taskName | Select-Object TaskName, State
}