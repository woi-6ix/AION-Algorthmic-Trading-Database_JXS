$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
$line = @(Get-Content '.env' | Where-Object { $_ -match '^\s*AION_TV_WEBHOOK_SECRET\s*=' } | Select-Object -Last 1)
if ($line.Count -ne 1) { throw 'Missing AION_TV_WEBHOOK_SECRET in .env' }
$secret = ($line[0] -split '=', 2)[1].Trim().Trim('"').Trim("'")
if ($secret.Length -lt 32) { throw 'Invalid webhook secret' }
$body = [ordered]@{
  webhook_secret = $secret
  strategy = 'KAIROS_V5'
  ticker = '{{ticker}}'
  timeframe = '{{interval}}'
  order_id = '{{strategy.order.id}}'
  order_action = '{{strategy.order.action}}'
  order_message = '{{strategy.order.alert_message}}'
  timenow = '{{timenow}}'
} | ConvertTo-Json -Compress -Depth 3
Set-Clipboard -Value $body
Write-Host 'TradingView order-fill JSON message copied to clipboard. Paste into the TradingView alert Message field.'
Write-Host 'DO NOT share the clipboard text, which includes your dedicated webhook secret.'
Write-Host 'Select ORDER FILLS ONLY, not order fills + alert() calls.'
