"""Read-only health check for the integrated AION dashboard (no external calls)."""
from __future__ import annotations
import json
from urllib.request import urlopen
from urllib.error import HTTPError, URLError

BASE='http://127.0.0.1:8501'
for route in ('/health','/lab/api/health','/lab/api/algorithms','/lab/api/backtests','/lab/api/data/status?symbol=SPY&interval=3m'):
    try:
        with urlopen(BASE+route,timeout=8) as response:
            body=response.read(15000)
            data=json.loads(body)
            status=200
            if route=='/lab/api/health':
                print('Backtest engine:',data.get('version','unknown'))
            if route=='/lab/api/backtests':
                print('Historical backtest runs:',len(data) if isinstance(data,list) else '?')
            print('PASS',status,route)
    except (HTTPError, URLError, TimeoutError, OSError, ValueError) as e:
        print('FAIL',route,type(e).__name__,str(e)[:140])
print('Checks completed. They do not place orders or trigger historical backtests.')
