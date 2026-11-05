"""Create a dedicated TradingView ingress secret; do not print it."""
from pathlib import Path
from secrets import token_urlsafe
from dotenv import dotenv_values, set_key

root = Path(__file__).resolve().parent
path = root / '.env'
if not path.exists():
    raise SystemExit('ERROR: .env not found. Run this in your existing AION-Execution folder.')
existing = dotenv_values(path).get('AION_TV_WEBHOOK_SECRET')
if existing and len(existing) >= 32:
    print('An existing TradingView webhook secret was preserved. No change required.')
else:
    set_key(str(path), 'AION_TV_WEBHOOK_SECRET', token_urlsafe(36))
    print('Generated a dedicated TradingView webhook secret in .env. Secret not displayed.')
print('Keep .env private. Do not upload it to GitHub or paste its contents into chat.')
