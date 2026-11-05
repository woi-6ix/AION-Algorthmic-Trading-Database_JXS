# Security and Trading Safety

- The repository is **public**. Do not commit `.env`, keys, real webhook URLs with secret query strings, TradingView message payloads with embedded secrets, account IDs, positions, or SQLite records.
- Local `.env` contains only user-owned credentials. Store securely and rotate if ever exposed.
- `aion_tv_gateway.py` currently authenticates using a shared secret included in the TradingView JSON payload. This alone does **not** prove the sender is TradingView; an origin-authenticated edge/relay and HTTPS request hardening are required before treating webhooks as trade-authoritative.
- Cloudflare Quick Tunnels have no uptime guarantee and hostnames can change; use only for supervised testing.
- Keep `AION_MODE=SIMULATION`. The only broker order execution is to hard-coded Alpaca **PAPER** API host.
- Paper trading does not reflect real options liquidity, spread costs, queue position or slippage. Indicative option quotes are not consolidated NBBO data.
- Always verify `status` and broker positions; **disarm does not liquidate** outstanding option positions or cancel orders.
- This strategy is intraday, focused on SPY 0DTE; options can lose 100% of their premium and expire before exits can be placed.
- Dashboard runs without built-in user authentication; restrict access through Tailscale Serve or provide hardened access control before any public deployment.
- Untrusted files and archived patches in `patches/` are historical build artifacts; do not blindly execute every installer.

## Responsible reporting

Don't open issues containing actual keys, webhook secrets, personal account identifiers, live order IDs, private tailnet addresses or sensitive transaction history. Revocation/rotation of exposed keys is urgent.
