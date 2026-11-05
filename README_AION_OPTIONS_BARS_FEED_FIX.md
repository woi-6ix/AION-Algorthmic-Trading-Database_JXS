# AION — Alpaca historical SPY options chart fix (2026-11-05)

## Confirmed cause

Read-only preflight returned HTTP 400, `unexpected query parameter(s): feed`, for every historical-options-bars request. The Alpaca endpoint `GET https://data.alpaca.markets/v1beta1/options/bars` **does not support** a `feed` parameter. Conversely, Alpaca's **option snapshots and latest quotes** endpoints support `feed=indicative`. The chart was inadvertently using the option-quotes parameter for historical aggregated trade bars.

API reference: https://docs.alpaca.markets/us/reference/optionbars

## Fix

- `aion_alpaca_paper_algo.py` `AlpacaPaper.bars()`: remove the invalid `feed` query parameter, cap the historical request to at least 20 minutes behind current UTC time for Basic-plan access, and stop at the actual option expiry session close. Keep all broker order/exit functions unchanged.
- `aion_option_preflight.py`: use valid GET-only chart probes (3Min, 1Min, 5Min; 3Min with no explicit `end`). No `feed` parameter. Preserve the redacted error handling.
- `aion_alpaca_paper_panel.py`: accurately describe the chart as **historical option trade bars**, which are different from the **indicative quotes** used for contract selection.
- `install_aion_bars_feed_fix.py`: backup-first, idempotent surgical installer. Refuses to run if the bot is armed; only replaces known chart code sections; rejects unexpected edits; no API calls or account modifications.
- `tests/test_alpaca_bars_feed_fix.py`: offline regression tests.

## Windows installation

1. **Disarm** your PAPER bot (stopping the PowerShell window does *not* disarm it):

   ```powershell
   cd C:\Users\sathw\AION-Execution
   .\.venv\Scripts\python.exe .\aion_alpaca_paper_algo.py disarm
   .\.venv\Scripts\python.exe .\aion_alpaca_paper_algo.py status
   ```

   Expected: `Paper bot armed: False`. Stop the bot worker using Ctrl+C if it is still running. Disarm **does not** cancel orders or close positions. Verify paper account state separately.

2. Download `AION_Alpaca_Historical_Options_Bars_Feed_Fix.zip` from the release and extract into `C:\Users\sathw\AION-Execution`:

   ```powershell
   Expand-Archive -Path "$env:USERPROFILE\Downloads\AION_Alpaca_Historical_Options_Bars_Feed_Fix.zip" -DestinationPath . -Force
   .\.venv\Scripts\python.exe .\install_aion_bars_feed_fix.py
   ```

   The installer prints the timestamped backup path and does **not** alter your `.env`, SQLite accounts or order history, execution journal, signal logic, or webhook secret.

3. Test the same expired contract from the failure (November 5, 2026):

   ```powershell
   .\.venv\Scripts\python.exe .\aion_option_preflight.py --symbol SPY261105C00736000
   .\.venv\Scripts\python.exe .\aion_alpaca_paper_algo.py chart --symbol SPY261105C00736000
   ```

   Successful chart requests can return **0 bars** when there were no trades or the account lacks coverage. A different HTTP code or error message should be inspected, **not** circumvented. Do not print API keys.

4. Restart only the dashboard on port 8501: stop its PowerShell window with Ctrl+C, then:

   ```powershell
   .\.venv\Scripts\python.exe -m uvicorn aion_live_dashboard:app --host 127.0.0.1 --port 8501
   ```

   Open `http://127.0.0.1:8501/alpaca-paper/`. Note: today's chain can be empty outside trading hours; the expired-contract chart can still be tested with the CLI.

5. The preflight also showed the **TradingView gateway on 8002 OFFLINE**. The chart fix does not restart services. Start it in a separate persistent PowerShell window, then re-establish the Cloudflare tunnel and verify TradingView's webhook URL. A GET to `/tv-webhook` should return HTTP 405. Do not replay past signals or enable paper automation based solely on that GET; confirm authenticated TradingView HTTP 200 and a matching AION event.

## Safety, limits and expected behavior

- The data host remains `data.alpaca.markets` and is **GET only**; the order host remains hardcoded to `paper-api.alpaca.markets` in the separate order worker.
- The **$2,500 total premium** ceiling, KAIROS V5-driven entries and exits, emergency cutoff, and the disarmed state are unchanged.
- TradingView alerts and Alpaca option charts are separate feeds. Historical option *trade bars* may lag by 15+ minutes; Alpaca's **indicative quote snapshots** are not OPRA and must not be confused with actual historical trades or guaranteed execution prices.
- An API `200` with no bars is possible if the contract did not trade during the interval, and does not establish that a plot can be populated.
- Local tests mocked Alpaca responses. No live credentials were used. **No orders were submitted** as part of this fix.