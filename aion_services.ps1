# Register and operate independent per-user Windows background tasks for AION.
# Default Install: dashboard launches at sign-in; worker supervisor is installed
# DISABLED until explicit EnableWorker (it NEVER arms the trading bot).
[CmdletBinding()]
param(
    [ValidateSet('Install','Status','StartDashboard','StopDashboard',
                 'EnableWorker','DisableWorker','StartWorker','StopWorker',
                 'CopyToken','Logs','Uninstall')]
    [string]$Action = 'Status',
    [switch]$ConfirmWorkerActivation
)
$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$python = Join-Path $root '.venv\Scripts\python.exe'
$pythonw = Join-Path $root '.venv\Scripts\pythonw.exe'
$ltm = Join-Path $root 'aion_ltm_background.py'
$supervisor = Join-Path $root 'aion_paper_supervisor.py'
$worker = Join-Path $root 'aion_alpaca_paper_algo.py'
$dashboardTask = 'AION-LTM-Dashboard'
$workerTask = 'AION-Alpaca-PAPER-Supervisor' # Required by existing LTM controls.

function Get-TaskOrNull([string]$Name) {
    return Get-ScheduledTask -TaskName $Name -ErrorAction SilentlyContinue
}
function Assert-Files([string[]]$Paths) {
    foreach ($path in $Paths) {
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw "Missing required file: $path" }
    }
}
function Assert-TaskMatches([string]$Name, [string]$File) {
    $task = Get-TaskOrNull $Name
    if ($null -eq $task) { return $false }
    $argsText = ($task.Actions | ForEach-Object { $_.Arguments }) -join ' '
    $exeText = ($task.Actions | ForEach-Object { $_.Execute }) -join ' '
    if (-not ($argsText.Contains($File) -and $exeText.Contains($pythonw))) {
        throw "Existing task '$Name' has a different action. Inspect it manually; refusing to overwrite."
    }
    return $true
}
function Register-AionTask([string]$Name, [string]$File, [int]$Restarts) {
    if (Assert-TaskMatches $Name $File) {
        Write-Host "$Name is already registered; keeping its current running/enabled state."
        return $false
    }
    $user = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
    $act = New-ScheduledTaskAction -Execute $pythonw -Argument ('"' + $File + '"' + $(if ($Name -eq $workerTask) { ' run' } else { '' })) -WorkingDirectory $root
    $trigger = New-ScheduledTaskTrigger -AtLogOn -User $user
    $principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
    $settingsParams = @{
        MultipleInstances = 'IgnoreNew'
        ExecutionTimeLimit = (New-TimeSpan -Seconds 0)
        RestartCount = $Restarts
        StartWhenAvailable = $true
        AllowStartIfOnBatteries = $true
        DontStopIfGoingOnBatteries = $true
    }
    if ($Restarts -gt 0) { $settingsParams.RestartInterval = (New-TimeSpan -Minutes 1) }
    $settings = New-ScheduledTaskSettingsSet @settingsParams
    Register-ScheduledTask -TaskName $Name -Action $act -Trigger $trigger `
        -Principal $principal -Settings $settings -Description 'AION per-user PAPER-only background component' | Out-Null
    return $true
}
function Get-ArmStatus {
    if (-not (Test-Path -LiteralPath $python) -or -not (Test-Path -LiteralPath $worker)) {
        return 'Worker CLI unavailable'
    }
    Push-Location $root
    try {
        return ((& $python $worker status 2>&1) -join "`n")
    } finally { Pop-Location }
}
function Assert-WorkerSafeToStop {
    $output = Get-ArmStatus
    if ($output -match 'Paper bot armed:\s*True' -and $output -notmatch 'Kill switch:\s*True') {
        throw 'Worker is ARMED. Pause automation and reconcile broker positions/orders first. Task stop could abandon active exits.'
    }
}
function Assert-WorkerActivation {
    if (-not $ConfirmWorkerActivation) {
        throw 'Worker supervisor can execute PAPER orders if ARMED. Retry with -ConfirmWorkerActivation after checking broker positions/orders, lock tests, and kill switch.'
    }
}
function Show-Task([string]$Name) {
    $task = Get-TaskOrNull $Name
    if ($null -eq $task) { Write-Host "$Name : NOT INSTALLED"; return }
    $info = Get-ScheduledTaskInfo -TaskName $Name -ErrorAction SilentlyContinue
    $result = if ($info) { " LastResult=$($info.LastTaskResult)" } else { '' }
    Write-Host "$Name : $($task.State)$result"
}
function Show-Status {
    Show-Task $dashboardTask
    Show-Task $workerTask
    try {
        $probe = Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:8501/aion-alpaca-controls.js' -TimeoutSec 3
        if ($probe.StatusCode -eq 200) { Write-Host 'LTM HTTP: control-enabled endpoint responding on 127.0.0.1:8501' }
        else { Write-Warning "LTM HTTP: unexpected status $($probe.StatusCode)" }
    } catch {
        Write-Warning 'LTM HTTP: unavailable, or another service owns port 8501. See logs.'
    }
    # Process table is independent of SQLite's saved 'armed' flag or old heartbeat.
    $procs = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue)
    $liveWorkers = @($procs | Where-Object { $_.CommandLine -match 'aion_alpaca_paper_algo\.py["'']?\s+run(?:\s|$)' })
    $liveSupervisors = @($procs | Where-Object { $_.CommandLine -match 'aion_paper_supervisor\.py["'']?\s+run(?:\s|$)' })
    Write-Host ("Worker processes: " + $liveWorkers.Count + ' [PIDs: ' + (($liveWorkers | ForEach-Object { $_.ProcessId }) -join ', ') + ']')
    Write-Host ("Supervisor processes: " + $liveSupervisors.Count + ' [PIDs: ' + (($liveSupervisors | ForEach-Object { $_.ProcessId }) -join ', ') + ']')
    if ($liveWorkers.Count -gt 1) { Write-Warning 'More than one worker process found: investigate before sending PAPER orders.' }
    if (Test-Path -LiteralPath $python -PathType Leaf) {
        if (Test-Path -LiteralPath $supervisor -PathType Leaf) {
            $stateFile = Join-Path $root '.aion_paper_supervisor_status.json'
            try {
                $data = Get-Content -LiteralPath $stateFile -Raw | ConvertFrom-Json
                $age = ([DateTimeOffset]::UtcNow - [DateTimeOffset]::Parse($data.updated_at)).TotalSeconds
                $fresh = ($age -ge -15 -and $age -le 15)
                if ($fresh) { Write-Host "Worker supervisor: $($data.mode); child PID=$($data.worker_pid)" }
                else { Write-Warning "Worker supervisor: OFFLINE (stale status, $([math]::Round($age))s old)" }
            } catch { Write-Warning 'Worker supervisor: NO FRESH STATUS' }
        }
        $bot = Get-ArmStatus
        Write-Host $bot
        $h = [regex]::Match($bot, 'Last heartbeat:\s*([^\r\n]+)')
        if ($h.Success) {
            try {
                $age = ([DateTimeOffset]::UtcNow - [DateTimeOffset]::Parse($h.Groups[1].Value.Trim())).TotalSeconds
                if ($age -ge -15 -and $age -le 90 -and $liveWorkers.Count -eq 1) { Write-Host "Worker heartbeat: FRESH; one worker process confirmed ($([math]::Round($age))s ago)" }
                elseif ($age -ge -15 -and $age -le 90) { Write-Warning 'Worker heartbeat recent but no unique worker process confirmed' }
                else { Write-Warning "Worker heartbeat: STALE ($([math]::Round($age))s ago)" }
            } catch { Write-Warning 'Worker heartbeat: missing or invalid' }
        }
    }
    Write-Host 'Note: ARMED means permission, not proof that the worker is running.'
}

switch ($Action) {
    'Install' {
        Assert-Files @($python, $pythonw, $ltm, $supervisor, $worker, (Join-Path $root 'aion_ltm_with_controls.py'))
        $freshDashboard = Register-AionTask $dashboardTask $ltm 3
        $freshWorker = Register-AionTask $workerTask $supervisor 0
        if ($freshWorker) {
            Disable-ScheduledTask -TaskName $workerTask | Out-Null
            Write-Host 'Worker supervisor registered DISABLED. It cannot start at logon until EnableWorker.'
        }
        $listeners = @(Get-NetTCPConnection -LocalPort 8501 -State Listen -ErrorAction SilentlyContinue)
        if ($listeners.Count -eq 0) {
            Start-ScheduledTask -TaskName $dashboardTask
            Write-Host 'Dashboard start requested. Its task will also launch at Windows sign-in.'
        } else {
            Write-Warning 'Port 8501 is already in use. Do NOT kill it blindly; stop the old manual dashboard first, then StartDashboard.'
        }
        Show-Status
    }
    'Status' { Show-Status }
    'StartDashboard' {
        Assert-Files @($pythonw, $ltm)
        if (@(Get-NetTCPConnection -LocalPort 8501 -State Listen -ErrorAction SilentlyContinue).Count -gt 0) {
            throw 'Port 8501 already has a listener. Identify/stop the old dashboard first; existing process left untouched.'
        }
        Start-ScheduledTask -TaskName $dashboardTask
        Write-Host 'Requested dashboard task start.'
    }
    'StopDashboard' {
        Stop-ScheduledTask -TaskName $dashboardTask
        Write-Host 'Stopped the dashboard task. PAPER worker/supervisor left unchanged.'
    }
    'EnableWorker' {
        Assert-WorkerActivation
        Enable-ScheduledTask -TaskName $workerTask | Out-Null
        Write-Host 'PAPER supervisor enabled for future Windows sign-ins. NOT armed or started by this command.'
    }
    'DisableWorker' {
        Disable-ScheduledTask -TaskName $workerTask | Out-Null
        Write-Host 'PAPER supervisor sign-in trigger disabled. Running worker, if any, left untouched.'
    }
    'StartWorker' {
        Assert-WorkerActivation
        $task = Get-TaskOrNull $workerTask
        if ($null -eq $task) { throw 'PAPER supervisor task not installed.' }
        if ($task.State -eq 'Disabled') { throw 'PAPER supervisor task disabled. Run EnableWorker deliberately first.' }
        Start-ScheduledTask -TaskName $workerTask
        Write-Host 'Supervisor start requested. If worker is ARMED, it can now place PAPER orders.'
    }
    'StopWorker' {
        Assert-WorkerSafeToStop
        Stop-ScheduledTask -TaskName $workerTask
        Write-Host 'Stopped supervisor task. Verify no child worker remains and reconcile paper orders.'
    }
    'CopyToken' {
        $token = Join-Path $env:LOCALAPPDATA 'AION\ltm-control-token.txt'
        Assert-Files @($token)
        (Get-Content -LiteralPath $token -Raw).Trim() | Set-Clipboard
        Write-Host 'Copied existing private LTM token to clipboard; token not printed.'
    }
    'Logs' {
        foreach ($name in @('aion-ltm-background.log','aion-paper-supervisor.log','aion-paper-worker-supervised.log')) {
            $path = Join-Path $root ('logs\' + $name)
            if (Test-Path -LiteralPath $path) { Write-Host "----- $name -----"; Get-Content -LiteralPath $path -Tail 20 }
        }
    }
    'Uninstall' {
        Assert-WorkerSafeToStop
        foreach ($name in @($workerTask, $dashboardTask)) {
            if (Get-TaskOrNull $name) {
                Stop-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
                Unregister-ScheduledTask -TaskName $name -Confirm:$false
                Write-Host "Removed task $name"
            }
        }
        Write-Host 'No broker state, passwords, or tokens were modified. Check for any remaining manual worker processes.'
    }
}
