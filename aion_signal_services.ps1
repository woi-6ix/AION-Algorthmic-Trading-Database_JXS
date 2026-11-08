# Manage the existing AION KAIROS V5 signal receiver (8001) and TradingView
# simulation-only gateway (8002) as separate per-user Windows logon tasks.
# Never controls Alpaca PAPER arming, brokerage positions, orders or keys.
[CmdletBinding()]
param([ValidateSet('Install','Status','Start','Stop','Logs','Uninstall')]
      [string]$Action='Status')
$ErrorActionPreference='Stop'
$root=$PSScriptRoot
$pythonw=Join-Path $root '.venv\Scripts\pythonw.exe'
$python=Join-Path $root '.venv\Scripts\python.exe'
$launcher=Join-Path $root 'aion_signal_background.py'
$signal=Join-Path $root 'aion_kairos_server.py'
$gateway=Join-Path $root 'aion_tv_gateway.py'
$taskReceiver='AION-KAIROS-V5-Signal-Receiver'
$taskGateway='AION-TV-V5-Simulation-Gateway'

function Assert-File([string]$path) {
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw "Missing required file: $path" }
}

function Read-Config([string]$name) {
    $processValue=[Environment]::GetEnvironmentVariable($name,'Process')
    if (-not [string]::IsNullOrWhiteSpace($processValue)) { return $processValue.Trim() }
    $envPath=Join-Path $root '.env'
    if (-not (Test-Path -LiteralPath $envPath -PathType Leaf)) { return '' }
    $matchLines=@(Get-Content -LiteralPath $envPath -Encoding UTF8 | Where-Object {
        $_ -match ('^\s*(?:export\s+)?'+[regex]::Escape($name)+'\s*=')
    })
    if ($matchLines.Count -eq 0) { return '' }
    $value=($matchLines[-1] -split '=',2)[1].Trim()
    if ($value.Length -ge 2 -and (($value.StartsWith('"') -and $value.EndsWith('"')) -or
        ($value.StartsWith("'") -and $value.EndsWith("'")))) { $value=$value.Substring(1,$value.Length-2) }
    return $value
}

function Assert-Ready {
    foreach ($p in @($python,$pythonw,$launcher,$signal,$gateway)) { Assert-File $p }
    $mode=Read-Config 'AION_MODE'
    $allowed=Read-Config 'AION_ALLOWED_STRATEGY'
    $secret=Read-Config 'AION_TV_WEBHOOK_SECRET'
    if ($mode -and $mode.ToUpperInvariant() -ne 'SIMULATION') {
        throw 'AION_MODE is not SIMULATION. Refusing to launch gateway.'
    }
    if ($allowed -and $allowed -ne 'KAIROS_V5') {
        throw 'AION_ALLOWED_STRATEGY is not KAIROS_V5.'
    }
    if ([string]::IsNullOrWhiteSpace($secret) -or $secret.Length -lt 32 -or
        $secret -match '^REPLACE_WITH') {
        throw 'Missing/invalid AION_TV_WEBHOOK_SECRET. Review .env or run configure_tv_secret.py locally; never share it.'
    }
    # Check source compilation without importing broker/receiver modules.
    & $python -m py_compile $launcher $signal $gateway
    if ($LASTEXITCODE -ne 0) { throw 'Python source compile check failed.' }
}

function Get-AionTask([string]$name) {
    return Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
}
function Assert-TaskAction([string]$name,[string]$kind) {
    $task=Get-AionTask $name
    if ($null -eq $task) { return $false }
    $actions=@($task.Actions)
    $expectedArgs='"'+$launcher+'" '+$kind
    if ($actions.Count -ne 1 -or
        $actions[0].Execute -ine $pythonw -or
        $actions[0].Arguments -ine $expectedArgs -or
        $actions[0].WorkingDirectory -ine $root) {
        throw "Existing task '$name' has a different action; inspect it, do not overwrite."
    }
    return $true
}
function Register-AionTask([string]$name,[string]$kind) {
    if (Assert-TaskAction $name $kind) {
        Write-Host "${name}: already registered; preserved."
        return
    }
    $user=[System.Security.Principal.WindowsIdentity]::GetCurrent().Name
    $actionObj=New-ScheduledTaskAction -Execute $pythonw -Argument ('"'+$launcher+'" '+$kind) -WorkingDirectory $root
    $trigger=New-ScheduledTaskTrigger -AtLogOn -User $user
    $principal=New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
    $settings=New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew `
        -ExecutionTimeLimit (New-TimeSpan -Seconds 0) -RestartCount 3 `
        -RestartInterval (New-TimeSpan -Minutes 1) -StartWhenAvailable `
        -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
    Register-ScheduledTask -TaskName $name -Action $actionObj -Trigger $trigger `
        -Principal $principal -Settings $settings -Description "AION KAIROS V5 SIMULATION ONLY: $kind (localhost)" | Out-Null
    Write-Host "${name}: task registered for Windows sign-in."
}

function Start-AionTask([string]$name,[string]$kind,[int]$port) {
    if (-not (Assert-TaskAction $name $kind)) { throw "Missing task: $name; run Install first." }
    $task=Get-AionTask $name
    if ($task.State -eq 'Running') {
        Write-Host "${name}: already Running."
        return
    }
    $listeners=@(Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue)
    if ($listeners.Count -gt 0) {
        $owners=($listeners | Select-Object -ExpandProperty OwningProcess -Unique) -join ','
        Write-Warning "Port $port is occupied by PID(s) $owners. Not starting another server; identify/stop manual server first."
        return
    }
    if ($task.State -eq 'Disabled') { throw "Task $name is Disabled. Enable it deliberately after inspection." }
    Start-ScheduledTask -TaskName $name
    Write-Host "${name}: start requested."
}

function Show-Status {
    foreach ($item in @(@($taskReceiver,8001),@($taskGateway,8002))) {
        $name=[string]$item[0]
        $port=[int]$item[1]
        $task=Get-AionTask $name
        $state=if ($null -eq $task) {'NOT INSTALLED'} else {$task.State.ToString()}
        $listeners=@(Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue)
        $owners=if($listeners.Count){($listeners | Select-Object -ExpandProperty OwningProcess -Unique) -join ','}else{'none'}
        Write-Host "${name}: $state | port $port listener PID(s): $owners"
    }
    try {
        $health=Invoke-RestMethod -Uri 'http://127.0.0.1:8001/health' -TimeoutSec 4
        if ($health.service -eq 'AION-KAIROS-V5' -and $health.mode -eq 'SIMULATION' -and
            $health.broker_orders_enabled -eq $false) {
            Write-Host 'Receiver /health: OK, SIMULATION, broker execution disabled'
        } else { Write-Warning 'Receiver /health returned unexpected identity or execution mode.' }
    } catch { Write-Warning 'Receiver /health unavailable.' }
    try {
        $result=Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:8002/tv-webhook' -TimeoutSec 4
        Write-Warning "Gateway GET /tv-webhook returned HTTP $($result.StatusCode); expected 405 (POST only)."
    } catch {
        if ($_.Exception.Response -and [int]$_.Exception.Response.StatusCode -eq 405) {
            Write-Host 'Gateway /tv-webhook: reachable (GET correctly rejected with HTTP 405)'
        } else { Write-Warning 'Gateway /tv-webhook unavailable or unexpected response.' }
    }
    Write-Host 'Trading state NOT changed. These services do not arm the Alpaca PAPER worker.'
}

switch($Action) {
    'Install' {
        Assert-Ready
        Register-AionTask $taskReceiver 'receiver'
        Register-AionTask $taskGateway 'gateway'
        Start-AionTask $taskReceiver 'receiver' 8001
        Start-Sleep -Seconds 3
        Start-AionTask $taskGateway 'gateway' 8002
        Start-Sleep -Seconds 3
        Show-Status
    }
    'Start' {
        Assert-Ready
        Start-AionTask $taskReceiver 'receiver' 8001
        Start-Sleep -Seconds 3
        Start-AionTask $taskGateway 'gateway' 8002
        Start-Sleep -Seconds 3
        Show-Status
    }
    'Status' { Show-Status }
    'Stop' {
        foreach ($pair in @(@($taskGateway,'gateway'),@($taskReceiver,'receiver'))) {
            if (Assert-TaskAction ([string]$pair[0]) ([string]$pair[1])) {
                Stop-ScheduledTask -TaskName ([string]$pair[0]) -ErrorAction SilentlyContinue
                Write-Host "Requested task stop: $($pair[0])"
            }
        }
        Write-Host 'Alpaca PAPER worker/supervisor left unchanged.'
    }
    'Logs' {
        foreach ($kind in @('receiver','gateway')) {
            $log=Join-Path $root ("logs\aion-$kind-background.log")
            if (Test-Path -LiteralPath $log) {
                Write-Host "=== $kind ==="
                Get-Content -LiteralPath $log -Tail 30
            } else { Write-Host "No $kind log yet." }
        }
    }
    'Uninstall' {
        foreach ($pair in @(@($taskGateway,'gateway'),@($taskReceiver,'receiver'))) {
            if (Assert-TaskAction ([string]$pair[0]) ([string]$pair[1])) {
                Stop-ScheduledTask -TaskName ([string]$pair[0]) -ErrorAction SilentlyContinue
                Unregister-ScheduledTask -TaskName ([string]$pair[0]) -Confirm:$false
                Write-Host "Unregistered $($pair[0])"
            }
        }
        Write-Host 'Existing LTM and PAPER worker services unaffected.'
    }
}
