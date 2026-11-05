> **Superseded as of November 5, 2026.** This is the original one-contract/$300 paper worker guide. The source has since been upgraded to a **$2,500 maximum total premium debit**, KAIROS V5-triggered exits and sequential reversals. Read [`README_AION_KAIROS_V5_2500_PREMIUM.md`](README_AION_KAIROS_V5_2500_PREMIUM.md) for current installation, risk controls and commands. Historical instructions below are archival and may be unsafe if followed as current.

# AION KAIROS V5 — ALPACA PAPER algorithmic 0DTE execution

This is **trade-capable code**, not a read-only brokerage connector. It consumes **accepted KAIROS V5 TradingView signals** from your existing AION simulator DB, selects a same-day SPY call/put, and can submit **actual paper orders** using **Alpaca's official Paper Trading API**. It never uses SnapTrade to place orders. Your SnapTrade-connected Webull CASH and MARGIN accounts are outside the entire execution path.

No paper orders are sent by installation, `check`, `chain`, chart loading, or `status`. The trade worker requires an explicit interactive `arm` command. No order has been submitted or verified in your account by the package author.

## Architecture

TradingView KAIROS V5 → authenticated Cloudflare tunnel → existing AION gateway port **8002** → existing simulator port **8001** → SQLite accepted events → **NEW Alpaca Paper strategy worker** → `https://paper-api.alpaca.markets/v2/orders` → poll broker fill → existing AION execution journal → existing mobile dashboard **8501**.

New read-only options chart in AION Backtesting tab mounted at `/alpaca-paper/`. The chart and dashboard **cannot submit or cancel orders**, even if accessible through your phone link. The trading worker remains a separate local PowerShell process and only uses paper API credentials from your private `.env`.

## Safety limits (hard-coded unless stated)

- **Paper-only base URL** is hardcoded. The program won't take an Alpaca live URL override and doesn't use Webull/SnapTrade credentials.
- **SPY ONLY**, **0DTE ONLY**, **1 contract per entry**, long calls or long puts only; only `BUY_TO_OPEN` and `SELL_TO_CLOSE`, limit DAY orders.
- **Maximum entry premium** $300 USD (1 contract × 100). Price from Alpaca **indicative** option quotes; strict quote timestamp and spread checks; indicative prices are *not* consolidated OPRA executable quotes.
- Entry 09:35–14:30 ET, exit order attempts 09:35–15:00 ET, automatic exit attempt starting 14:45 ET (if worker running, broker available, quotes fresh).
- Configurable demonstration **option-premium P&L triggers**: take-profit +30% (`AION_PAPER_TAKE_PROFIT_PCT=0.30`), stop -25% (`AION_PAPER_STOP_LOSS_PCT=0.25`). THESE ARE PAPER DEFAULTS, NOT EXACT KAIROS PINE R-MULTIPLES.
- Blocks unrecognized SPY option positions and all existing open SPY option orders; never opens an opposite position without prior close.
- Signals must have been validated by the existing gateway and logged after `arm`, no older than **55 seconds**. Duplicate event ids are ignored.
- Durable unique client order IDs and SQLite order reservation. A lost network response is considered **UNKNOWN** and *never automatically resubmitted*.
- A manual kill switch file `AION_PAPER_KILL_SWITCH.txt` or `disarm` stops **new automation**, **not** a currently open option position or broker order. Check the broker immediately after stopping.
- Fill journal entries require an actual Alpaca Paper `filled_qty` and `filled_avg_price` from the paper broker. Fees are set to zero **only because they are not verified**, so AION's P&L may be overstated; the entry is labeled `ALPACA_PAPER_API_FEES_NOT_CONFIRMED`.

The order worker does NOT place an order on install or import. The API calls are inside the worker's explicit `run` / `once` commands when **armed**. Paper fills may be unrealistic versus exchange markets; no results here imply a profitable strategy. This is an initial forward-test bot, not production-grade risk management.

## Installation on the current Windows AION machine

1. Save the ZIP to Downloads.
2. Extract into your existing `C:\Users\sathw\AION-Execution` folder:

```powershell
cd C:\Users\sathw\AION-Execution
Expand-Archive -Path "$env:USERPROFILE\Downloads\AION_KAIROS_V5_Alpaca_Paper_AutoTrader.zip" -DestinationPath . -Force
.\.venv\Scripts\python.exe -m pip install -r .\requirements-alpaca-paper-algo.txt
.\.venv\Scripts\python.exe .\install_aion_alpaca_paper_algo.py
```

Installer backs up modified `aion_live_dashboard.py` and `static/index.html` under `aion-alpaca-paper-backups\<timestamp>`. It does **not** modify existing `aion_tv_gateway.py`, `aion_kairos_server.py`, `aion_execution_journal.py`, `aion_kairos_v5_signals.sqlite3`, any past backtest data, or your `.env`.

3. Ensure your existing `.env` has **only your Alpaca PAPER credentials** for the new bot:

```dotenv
AION_MODE=SIMULATION
ALPACA_PAPER_API_KEY=your_paper_key_id
ALPACA_PAPER_SECRET_KEY=your_paper_secret
# Optional testing-only premium exit triggers:
AION_PAPER_TAKE_PROFIT_PCT=0.30
AION_PAPER_STOP_LOSS_PCT=0.25
```

**Never** paste credentials, `.env`, tunnel webhook secret or SnapTrade secrets into chat. The `.env` shouldn't be checked into Git. AION_MODE remains `SIMULATION` because 8001 and 8002 must remain signal-only, while the separate Alpaca PAPER worker is capable of paper order execution.

4. Restart **only the dashboard 8501** to see the new options panel:

```powershell
.\.venv\Scripts\python.exe -m uvicorn aion_live_dashboard:app --host 127.0.0.1 --port 8501
```

Open existing AION mobile URL → **Backtesting** → **Alpaca PAPER — SPY 0DTE options chain & chart**. For a direct local view, open `http://127.0.0.1:8501/alpaca-paper/`.

5. Read-only safety checks (no orders):

```powershell
.\.venv\Scripts\python.exe .\aion_alpaca_paper_algo.py check
.\.venv\Scripts\python.exe .\aion_alpaca_paper_algo.py status
.\.venv\Scripts\python.exe .\aion_alpaca_paper_algo.py chain
```

`chain` requires the US regular market to be open and includes call/put 0DTE candidate quote checks; the free **indicative** feed can be delayed or modified. If `chain` reports stale/missing quotes, **do not enable** paper order routing until market data works.

6. For a **supervised** paper trading test only, during market hours:

```powershell
.\.venv\Scripts\python.exe .\aion_alpaca_paper_algo.py arm
# Type the exact confirmation phrase in your local PowerShell window.
.\.venv\Scripts\python.exe .\aion_alpaca_paper_algo.py run
```

`arm` records your paper account's unique ID locally and sets an activation timestamp; the worker ignores all TradingView signals from before that point. **Keep this PowerShell window open.** Watch Alpaca Paper's Orders/Positions panel as the next actual KAIROS signal fires. Check the worker output and AION fill journal. A submit acknowledgement is not a fill. There is no market order fallback and paper limit orders may remain unfilled.

7. To **stop** algorithmic submission:

```powershell
.\.venv\Scripts\python.exe .\aion_alpaca_paper_algo.py disarm
```

You can also create `AION_PAPER_KILL_SWITCH.txt` inside AION-Execution. **Neither method automatically cancels open orders or closes open positions.** Always check the PAPER account and manually close positions or cancel open orders if appropriate. Stop the worker with Ctrl+C. To start it again, inspect/reconcile all historical paper bot orders and re-arm explicitly.

8. Reconcile broker-confirmed paper fills (can be called with worker stopped):

```powershell
.\.venv\Scripts\python.exe .\aion_alpaca_paper_algo.py sync
```

Order details come from Alpaca PAPER, not your Webull account. In AION, `Source=ALPACA_PAPER_API_FEES_NOT_CONFIRMED` distinguishes them from synthetic demo fills.

## Chart note

The new AION `/alpaca-paper/` tab shows TODAY's SPY paper option chain and selected 3-minute **options trade price** bars from Alpaca's indicative feed; it does not populate your historical `/lab` CSV quote-replay cache, whose `option_contracts_cached` will remain 0 until you import eligible historical quote pairs or adapt the research cache. An empty chart after hours can be a data-entitlement or market-hours condition, not a dashboard bug.

## Important restrictions / caveats

- This requires valid *Alpaca Paper API key+secret*, not a SnapTrade portal session. The SnapTrade plugin is **not** the execution connector for this bot.
- It is not the full Pine algorithm port. Entries/exits are **only** your KAIROS V5 TradingView strategy alerts, plus the simple premium % risk exits and cutoff.
- It checks the Alpaca Paper market clock in addition to its stricter ET window. It does not yet implement a full options holiday/early-close scheduler, robust quote streaming, trade-update websocket, or guaranteed liquidation.
- Because 0DTE options may auto-exercise at expiry, **supervise the initial runs and close in Alpaca Paper manually if the automation stalls**. Never leave open contracts unattended while testing.
- No real Alpaca broker or Webull orders are reachable via the hard-coded paper URL. Never alter the URL constant to a live endpoint.
- Run the worker **once**, not in multiple terminals. The program attempts an operating-system process lock.

## Troubleshooting

- `Paper account requires ... level 2`: verify options are enabled in your Alpaca PAPER dashboard.
- `SPY underlying quote too old`: stock IEX snapshot isn't fresh/entitled; do not trade.
- `No near-ATM ...`: options indicative quote feed missing, stale, spreads too wide, or the $300 budget blocks all contracts; wait for valid quotes. Do not blindly increase limits.
- `Order outcome UNKNOWN`: check Alpaca Paper Orders, then use `sync`, and inspect the local order DB. The bot will not resubmit on its own.
- `worker disarmed / kill switch`: check positions and pending orders manually; never assume disarming flattened positions.
- `404 /alpaca-paper/`: install and restart 8501. This does not require changing Cloudflare 8002 or Tailscale configuration.

## Documentation used

- Alpaca options enablement and orders: https://docs.alpaca.markets/us/docs/options-trading
- Paper order endpoint: https://docs.alpaca.markets/us/reference/postorder
- Option contracts: https://docs.alpaca.markets/us/reference/get-options-contracts
- Indicative options snapshots: https://docs.alpaca.markets/us/reference/optionsnapshots
- 3m option bars: https://docs.alpaca.markets/us/reference/optionbars
- Order by client ID: https://docs.alpaca.markets/us/reference/getorderbyclientorderid
- Expiration and exercise risk: https://docs.alpaca.markets/us/docs/options-trading-overview