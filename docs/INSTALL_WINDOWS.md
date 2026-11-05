# Windows installation and launch

## New clone (recommended)

Prerequisites: Windows PowerShell, Python 3.11+, Git, Tailscale for private phone access, and optional Cloudflare tunnel for authenticated TradingView requests.

```powershell
git clone https://github.com/woi-6ix/AION-Algorthmic-Trading-Database_JXS.git
cd AION-Algorthmic-Trading-Database_JXS
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-aion.txt
.\.venv\Scripts\python.exe -m pip install -r requirements-aion-unified.txt
.\.venv\Scripts\python.exe -m pip install -r requirements-alpaca-paper-algo.txt
Copy-Item .env.example .env
notepad .env
```

Configure only local **paper** keys and a strong dedicated webhook secret. Keep all credentials outside GitHub. Run each Uvicorn service in its own PowerShell window as shown in the root README.

If cloning a fresh folder, any existing local trade history is **not** migrated automatically. The following files are local-only and intentionally excluded: `aion_kairos_v5_signals.sqlite3`, `aion_execution_journal.sqlite3`, `aion_alpaca_paper_algo.sqlite3`, `.env`, and any older backtesting database. Make secure local backups when needed.

## Existing `C:\Users\sathw\AION-Execution`

**Do not blindly `git clone` or force-overwrite your actively running folder.** Stop the worker or disarm before updates, back up working data and credentials, and compare the GitHub snapshot against existing scripts before selectively copying changed files. The GitHub snapshot comes from the development packages and may not include any local-only edits or patches made since then.

Existing service ports: 8001 signal engine; 8002 webhook gateway; 8501 dashboard. The private Tailscale URL maps to dashboard only. A public Cloudflare Quick Tunnel, when used in development, should map only to the webhook gateway.

## Tests

```powershell
.\.venv\Scripts\python.exe .\check_aion_unified.py
.\.venv\Scripts\python.exe .\aion_alpaca_paper_algo.py check
.\.venv\Scripts\python.exe .\aion_alpaca_paper_algo.py chain
```

Do **not** run `arm` or `run` as part of basic setup. Verify the chart fix, options-data recency, and `disarm` status first.

## KAIROS V5 $2,500 premium paper upgrade (Nov 5, 2026)

For an EXISTING AION-Execution install, never overwrite running paper code. Disarm the old worker, stop the worker and port 8001; check for any open Alpaca PAPER positions. Then follow the backup-first installer steps in [`README_AION_KAIROS_V5_2500_PREMIUM.md`](../README_AION_KAIROS_V5_2500_PREMIUM.md). This installer preserves SQLite files and `.env`. Restart only port 8001 and 8501 (the unchanged gateway on 8002 and Cloudflare can remain running). Re-arm only after read-only verification, and supervise initial paper orders.