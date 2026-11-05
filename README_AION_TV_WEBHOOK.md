# AION KAIROS V5 - Secure TradingView Webhook Gateway (SIMULATION ONLY)

**This package does not place trades.** It modifies no Pine Script strategy rules. It receives TradingView V5 *strategy order-fill* events, authenticates their dedicated webhook secret, maps the existing V5 `strategy.order.alert_message` to CALL, PUT, or EXIT *intent*, and forwards events to your local AION V5 simulator. Both services refuse live brokerage execution.

## Architecture

- AION V5 simulator: `127.0.0.1:8001`; routes `/health`, `/webhook`, `/status`, `/events` — **localhost only, never exposed publicly**.
- AION TV gateway: `127.0.0.1:8002`; **only** `POST /tv-webhook` — expose this service with a temporary HTTPS tunnel during testing.
- Cloudflare Quick Tunnel: temporary external `https://<random>.trycloudflare.com/tv-webhook` URL. Browser access yields 404/405 and is not a public dashboard.

## Setup

1. Keep your existing AION server running on port 8001. Extract this ZIP **into the existing `C:\Users\sathw\AION-Execution` folder**, next to `.env`. Existing `.env` and SnapTrade scripts must be retained; ZIP contains no `.env` or API keys.
2. In PowerShell in that folder:

   ```powershell
   .\.venv\Scripts\python.exe -m pip install -r requirements-aion.txt
   .\.venv\Scripts\python.exe configure_tv_secret.py
   ```

   The generated `AION_TV_WEBHOOK_SECRET` is specific to TradingView alerts and **is not your SnapTrade API key**. It is stored in `.env` and never printed by the script. Keep `AION_MODE=SIMULATION` and `AION_ALLOWED_STRATEGY=KAIROS_V5`.
3. Open a second PowerShell window and start the gateway (the original simulator must remain on 8001):

   ```powershell
   cd C:\Users\sathw\AION-Execution
   .\.venv\Scripts\python.exe -m uvicorn aion_tv_gateway:app --host 127.0.0.1 --port 8002
   ```

4. Open another PowerShell window and test the gateway:

   ```powershell
   cd C:\Users\sathw\AION-Execution
   powershell -NoProfile -ExecutionPolicy Bypass -File .\test_tv_gateway.ps1
   ```

   Expected `result`: `SIGNAL_LOGGED` or `IGNORED_ALREADY_SIGNALED` if a previous local CALL intent exists; `broker_order_sent: False`.
5. Install Cloudflare's official `cloudflared` on Windows (https://developers.cloudflare.com/tunnel/downloads/), then in a fourth PowerShell window:

   ```powershell
   cloudflared tunnel --url http://127.0.0.1:8002
   ```

   This prints a temporary `https://...trycloudflare.com` URL. Append `/tv-webhook` in TradingView's webhook field. Don't append `:8002` to the external URL. Quick Tunnels change hostnames on restart and are for **testing only**, not reliable production execution.
6. Run the clipboard helper to copy the alert JSON **locally without printing the secret**:

   ```powershell
   powershell -NoProfile -ExecutionPolicy Bypass -File .\copy_tv_alert_message.ps1
   ```

   Paste into TradingView's alert **Message** field; do not print or share this JSON. The message includes a dedicated webhook token, **not** any broker credential.
7. In TradingView open `AMEX:SPY` on a **3-minute standard candlestick** chart with source `KAIROS_LC_Swing_Engine_V5_JXS_918.pine` added. Create an alert on that **strategy**, select **Order fills only** (NOT the combined `alert()` option), enable webhook, supply the temporary URL ending `/tv-webhook`, and paste the clipboard JSON into the alert **Message** field. Two-factor authentication must be enabled on your TradingView account.
8. Confirm exact strategy/version and options, run in SIMULATION, and inspect local `http://127.0.0.1:8001/events` when a future **real** V5 strategy order-fill alert fires. A TradingView alert created earlier for V4/V4.1.2 will never become V5; create a NEW V5 alert. Keep other strategy alerts from pointing at this new URL.

## Existing V5 alert translation

From your actual GitHub file `KAIROS_LC_Swing_Engine_V5_JXS_918.pine`:

| V5 order-fill alert_message prefix | AION event |
|---|---|
| `KAIROS LC V5 LONG |` | BUY_CALL intent |
| `KAIROS LC V5 SHORT |` | BUY_PUT intent |
| `KAIROS LC V5 LONG EXIT |` | EXIT intent |
| `KAIROS LC V5 SHORT EXIT |` | EXIT intent |
| `KAIROS LC V5 CLOSE LONG - KERNEL FLIP |` | EXIT intent |
| `KAIROS LC V5 CLOSE SHORT - KERNEL FLIP |` | EXIT intent |
| `KAIROS LC V5 SESSION CUTOFF |` | EXIT intent |

The source also has separate realtime `alert()` filled-entry notifications. Using **Order fills only** excludes those extra notifications, avoiding duplicate intent.

TradingView placeholders inserted in the alert message include `{{ticker}}`, `{{interval}}`, `{{strategy.order.id}}`, `{{strategy.order.action}}`, `{{strategy.order.alert_message}}`, `{{timenow}}`. Some nested placeholders inside the existing Pine `alert_message` text are not relied on for parsing.

## Security boundaries

- Gateway accepts only `/tv-webhook` POST, valid JSON, a strong secret, KAIROS_V5, SPY 3-minute chart, mapped V5 order messages, and a timestamp no older than 180 seconds.
- Request replay/delivery retries share a deterministic event ID in AION's SQLite DB; duplicates won't create a second signal intent.
- TradingView may retry failing webhooks (5xx); database deduplication avoids extra intent transitions from retry.
- A private webhook secret in TradingView's message body is a temporary ingress-authentication measure **only for this non-executing simulator**. It does not verify TradingView's mutual TLS client certificate or guarantee sole origin. Use an allowlisted, authenticated production relay/edge policy with appropriate webhook origin validation before enabling broker access.
- Exposing the gateway with a Quick Tunnel makes this **single webhook endpoint publicly reachable**. Never tunnel port 8001 or any broker/debug admin endpoint. Do not upload `.env`, share its contents, or paste the actual alert message into ChatGPT.
- When you restart the tunnel, update the TradingView alert with the new URL. Stop `cloudflared` to end temporary public access.
- Do not use this deployment for live 0DTE trading. Cloudflare Quick Tunnels have no uptime SLA; the simulator does not implement exchange-calendar session enforcement, order lifecycle, option chain, broker authorization, durable delivery or fail-safe exits.

## Troubleshooting

- `404 /` on gateway: expected, the public gateway has no homepage.
- `403 /tv-webhook`: incorrect webhook secret.
- `422 /tv-webhook`: wrong V5 message, missing TradingView placeholders, invalid timestamp or bad chart/timeframe.
- `503 /tv-webhook`: port 8001 simulator unavailable or its response timed out. Check the simulator terminal.
- `IGNORED_ALREADY_SIGNALED`: CALL_INTENT previously logged; expected on repeated long signals. It is not a brokerage fill.
- No real alerts: strategy may not generate a new order-fill until entry conditions occur; inspect TradingView alert log and webhook delivery status.

## Clear test intent before receiving real TradingView alerts

Your earlier `test_aion.ps1` generated `CALL_INTENT`. If a real short alert arrives before an EXIT, the simulator correctly blocks it as an opposing intent. To begin a fresh **simulation** run, while no alerts are firing:

```powershell
.\.venv\Scripts\python.exe reset_simulation_state.py
```

Type `RESET_SIMULATION` when prompted. This resets the simulator's **directional intent only** to FLAT; old event records remain for auditing. It does not change a Webull account or position.
