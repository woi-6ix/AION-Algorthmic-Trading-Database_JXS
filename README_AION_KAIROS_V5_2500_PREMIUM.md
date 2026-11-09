# AION — KAIROS V5 $2,500-premium Alpaca Paper execution (Nov 5, 2026)

**Scope:** KAIROS V5 TradingView strategy fills → authenticated gateway (8002) → local signal receiver (8001) → **Alpaca PAPER** worker → broker-confirmed fills → AION mobile dashboard (8501). The only order submission destination is the hardcoded `https://paper-api.alpaca.markets`. This patch is not a production/live trading system.

## KAIROS-aligned behavior

- `KAIROS LC V5 LONG` → buy same-day SPY calls; `KAIROS LC V5 SHORT` → buy same-day SPY puts. Each accepted, **fresh** signal may open at most one paper long-option position.
- **$2,500 maximum option premium debit per entry**, across **all contracts combined**, excluding unknown fees. Formula: `contracts = min(25, floor(min(2500, paper_cash) / (100 * limit_price)))`. If the selected near-ATM option is unaffordable for one contract, skip it; do not force a trade. Example: ask $2.56, limit $2.57 → 9 × 100 × $2.57 = **$2,313** maximum entry debit. A full premium loss is possible.
- Actual KAIROS V5 `LONG EXIT`, `SHORT EXIT`, `CLOSE ... KERNEL FLIP`, and `SESSION CUTOFF` messages map to **SELL_TO_CLOSE** for the *filled quantity* in that exact SPY option.
- No independent +30% take-profit or −25% stop-loss. Existing `AION_PAPER_TAKE_PROFIT_PCT` and `AION_PAPER_STOP_LOSS_PCT` environment settings are ignored by this version.
- When Pine **reverses directly**, AION logs `REVERSAL_INTENT_LOGGED`, closes the existing paper contract and then—only after broker confirmation, while signal still fresh—opens the opposite right. If closure is slow (>55 seconds), **skip the new entry** rather than chase an outdated signal.
- When an EXIT is received during an outstanding paper entry, the worker tries to cancel the remaining entry, waits for broker confirmation and closes only already-filled contracts. If cancellation/fill is ambiguous, halt and inspect paper positions manually.
- **Emergency intraday cutoff at 14:45 ET remains** to reduce 0DTE expiration risk. It is a safety exception to strictly strategy-driven exits. This is only an *attempted* close; neither limit orders nor software guarantee liquidation. Entry window is 09:31–14:45 ET (14:45 exclusive), matching the verified local Windows worker. No new entries are allowed once the 14:45 emergency-close threshold begins. EXIT submissions are permitted until 15:02 ET to accommodate a 15:00 KAIROS cutoff alert, although the emergency 14:45 close is intended to flatten much earlier. Neither paper limit fills nor session exit execution are guaranteed.
- New orders are never sent to Webull Canada CASH/MARGIN, SnapTrade or Alpaca Live. The worker refuses `AION_MODE` other than `SIMULATION` and can place **paper** orders only when explicitly armed. A free **indicative** options quote is not an executable OPRA quote, so paper fills/P&L are not reliable performance estimates.

## Before installing: stop an ARMED bot

Your prior status displayed `Paper bot armed: True`. **Do not overwrite the worker while it is active.** In a new PowerShell window:

```powershell
cd C:\Users\sathw\AION-Execution
.\.venv\Scripts\python.exe .\aion_alpaca_paper_algo.py disarm
.\.venv\Scripts\python.exe .\aion_alpaca_paper_algo.py status
```

The output must say `Paper bot armed: False`. Stop the PowerShell window running `aion_alpaca_paper_algo.py run` with Ctrl+C. Verify **no open SPY options positions or pending orders** in your Alpaca Paper account. Disarming does not liquidate or cancel existing orders. Stop the KAIROS simulator on port `8001` (Ctrl+C); leave the gateway/tunnel alone.

## Install with backups

Download `AION_KAIROS_V5_2500_Premium_Paper_Upgrade.zip`, save it to Downloads, then:

```powershell
cd C:\Users\sathw\AION-Execution
Expand-Archive -Path "$env:USERPROFILE\Downloads\AION_KAIROS_V5_2500_Premium_Paper_Upgrade.zip" -DestinationPath . -Force
.\.venv\Scripts\python.exe .\install_aion_kairos_v5_2500.py
```

The installer **refuses if still armed**, checks known source versions, compiles the replacement Python files, and backs up the four affected source files under `aion-kairos-2500-backups/<timestamp>`. It **does not change** `.env`, `AION_MODE`, SQLite event/order/fill history, SnapTrade configuration, logo, research, webhooks, the official KAIROS Pine file or any real broker setting.

After install, restart **8001**:

```powershell
.\.venv\Scripts\python.exe -m uvicorn aion_kairos_server:app --host 127.0.0.1 --port 8001
```

Restart dashboard **8501** in a separate PowerShell window (Ctrl+C in old dashboard first):

```powershell
cd C:\Users\sathw\AION-Execution
.\.venv\Scripts\python.exe -m uvicorn aion_live_dashboard:app --host 127.0.0.1 --port 8501
```

The webhook gateway **8002** and Cloudflare tunnel can remain running because the JSON schema and gateway route are unchanged.

## Verification: no orders yet

```powershell
cd C:\Users\sathw\AION-Execution
.\.venv\Scripts\python.exe .\aion_alpaca_paper_algo.py status
.\.venv\Scripts\python.exe .\aion_alpaca_paper_algo.py check
.\.venv\Scripts\python.exe .\aion_alpaca_paper_algo.py chain
.\.venv\Scripts\python.exe .\aion_alpaca_paper_algo.py chart --symbol SPY261105C00736000
```

`chart` with the example symbol retrieves Nov 5 historical bars when permitted; change the symbol on a subsequent date. With the free plan, historical option bars can lag by 15 minutes, return no bars, or still give HTTP 400 if the subscription/parameters aren't accepted. The chart-only fix from the previous release is retained; this release **does not guarantee** that the HTTP 400 issue is resolved for your account.

Local read-only chart: `http://127.0.0.1:8501/alpaca-paper/`; Tailscale link: `https://jxs-hpx360.tailf4db21.ts.net/` (requires laptop and Tailscale running). Check `Paper bot armed: False` until you have verified the broker and signal flow. The installed account-binding database is retained, and a fresh `arm` records a new event cutoff so **old** alerts aren't replayed.

## For supervised PAPER execution (after all checks)

```powershell
.\.venv\Scripts\python.exe .\aion_alpaca_paper_algo.py arm
# Confirm ONLY when happy with $2,500 max paper premium per entry.
.\.venv\Scripts\python.exe .\aion_alpaca_paper_algo.py run
```

Keep it supervised in Alpaca Paper. Watch the first **BUY_TO_OPEN → KAIROS EXIT → SELL_TO_CLOSE** cycle. In a separate window, `... aion_alpaca_paper_algo.py status` and `... sync` reconcile broker-filled orders. If an order is partially filled, stuck, canceled, rejected or the worker stops, examine Alpaca Paper directly. **Paper buying power is not the same as risk per trade.** Limit orders may remain unfilled; browser/PC/tunnel/network outages can miss alerts. Do not rely on an unattended laptop for 0DTE expiration protection.

## Operational constraints and testing

- **One managed SPY option position at a time**, matching broker holdings to confirmed local fills. Any unexpected/manual SPY option exposure halts the worker.
- Durable SQLite client order IDs prevent resubmission after unknown network outcomes; staged exits and reversals are persisted and only use paper API.
- Partial fills journaled incrementally and deterministically for dashboard FIFO P&L. Broker-provided per-fill fees are not available; journal fees remain zero and reported P&L may be overstated.
- Source-level tests are fully offline/mocked and never connect to Alpaca. Run optionally after installing `pytest`: `.\.venv\Scripts\python.exe -m pytest -q tests\test_kairos_v5_paper_budget.py`.
- No TradingView alerts were changed or triggered by this upgrade, and no actual paper orders were submitted during creation of this release.

## Source control

All modified source, documentation, and this installer are in `woi-6ix/AION-Algorthmic-Trading-Database_JXS`. The ZIP is also archived under `patches/release_archives/`. **Never commit** `.env`, paper API keys, TradingView webhook secrets, account numbers, SQLite databases, logs or real trade-history data to the public repo.