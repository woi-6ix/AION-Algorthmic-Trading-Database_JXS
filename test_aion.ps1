$root = 'http://127.0.0.1:8001'
Write-Host 'Health:'
Invoke-RestMethod "$root/health" | Format-List
$body = @{
  event_id = ('localtest-v5-' + [guid]::NewGuid().ToString('N'))
  strategy = 'KAIROS_V5'
  symbol = 'SPY'
  timeframe = '3m'
  signal = 'BUY_CALL'
  emitted_at = [DateTime]::UtcNow.ToString('yyyy-MM-ddTHH:mm:ssZ')
} | ConvertTo-Json
Write-Host 'Sending one TEST signal (no brokerage order):'
Invoke-RestMethod -Method Post -Uri "$root/webhook" -Body $body -ContentType 'application/json' | Format-List
Write-Host 'Status:'
Invoke-RestMethod "$root/status" | Format-List
