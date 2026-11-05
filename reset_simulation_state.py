"""Resets simulation-only directional intent; never interacts with broker."""
from pathlib import Path
from dotenv import dotenv_values
import sqlite3

root = Path(__file__).resolve().parent
config = dotenv_values(root / '.env')
if str(config.get('AION_MODE','SIMULATION')).upper() != 'SIMULATION':
    raise SystemExit('Refusing reset: AION_MODE must be SIMULATION')
db = root / 'aion_kairos_v5_signals.sqlite3'
if not db.exists():
    raise SystemExit('Simulator database not found. Nothing to reset.')
confirmation = input('Reset ONLY KAIROS V5 simulation intent to FLAT? Type RESET_SIMULATION: ').strip()
if confirmation != 'RESET_SIMULATION':
    raise SystemExit('Canceled. No changes made.')
with sqlite3.connect(db, timeout=10, isolation_level=None) as conn:
    conn.execute('BEGIN IMMEDIATE')
    conn.execute("UPDATE state SET intent_state = 'FLAT' WHERE id = 1")
    conn.execute('COMMIT')
print('Simulation intent reset to FLAT. Event history preserved. No brokerage action.')
