$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
$line = @(Get-Content '.env' | Where-Object { $_ -match '^\s*AION_TV_WEBHOOK_SECRET\s*=' } | Select-Object -Last 1)
if ($line.Count -ne 1) { throw 'Missing AION_TV_WEBHOOK_SECRET in .env' }
$secret = ($line[0] -split '=', 2)[1].Trim().Trim('"').Trim("'")
if ($secret.Length -lt 32) { throw 'Webhook secret must contain at least 32 characters' }
$now = [DateTime]::UtcNow.ToString('yyyy-MM-ddTHH:mm:ssZ')
$payload = @{
  webhook_secret = $secret
  strategy = 'KAIROS_V5'
  ticker = 'SPY'
  timeframe = '3'
  order_id = 'AION_LOCAL_TEST_' + [Guid]::NewGuid().ToString('N')
  order_action = 'buy'
  order_message = 'KAIROS LC V5 LONG | SPY @ 100.00'
  timenow = $now
} | ConvertTo-Json -Compress
Write-Host 'Testing real V5 message format through the secured gateway (simulator only)...'
Invoke-RestMethod -Method Post -Uri 'http://127.0.0.1:8002/tv-webhook' -Body $payload -ContentType 'application/json' | Format-List
Write-Host 'Local AION status:'
Invoke-RestMethod 'http://127.0.0.1:8001/status' | Format-List
Write-Host 'No brokerage orders can be placed by either service.'
