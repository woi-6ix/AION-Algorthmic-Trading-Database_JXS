"""ARGUS Terminal — dedicated READ-ONLY MCP server (stdio).

Never imports the trading worker or its controls. The OpenAI Secure MCP Tunnel
should launch this stdio server from the user's Windows computer. It has no
server port, HTTP listener, order submission, arming or file-writing tools.

Requires the official Python MCP SDK v2: pip install "mcp>=2,<3"
"""
from __future__ import annotations

from mcp.server import MCPServer
import argus_readonly_data as data

mcp = MCPServer(
    "ARGUS Terminal — Read Only",
    instructions=(
        "Read-only diagnostics for the user's local ARGUS Terminal. "
        "These tools cannot place or cancel trades, arm/disarm a worker, "
        "clear kill switches, update files, or query broker credentials. "
        "Distinguish local signals from confirmed fills and never assume "
        "the local worker is running merely because it is armed."
    ),
)


@mcp.tool()
def argus_health() -> dict:
    """Check localhost gateway/receiver/dashboard and PAPER supervisor, arm state, kill switch and heartbeat. Read-only."""
    return data.health()


@mcp.tool()
def argus_diagnostics() -> dict:
    """Summarize ARGUS health, latest signals, worker decisions, PAPER order records and fill journal. Read-only."""
    return data.diagnostics()


@mcp.tool()
def argus_signal_log(limit: int = 15) -> dict:
    """Read accepted/rejected TradingView signal events from local SQLite; does not return webhook payloads. Read-only."""
    return data.signal_log(limit)


@mcp.tool()
def argus_worker_decisions(limit: int = 15) -> dict:
    """Read the PAPER worker's recorded processing outcomes; cannot trigger or replay signals. Read-only."""
    return data.worker_decisions(limit)


@mcp.tool()
def argus_paper_orders(limit: int = 15) -> dict:
    """Read local Alpaca PAPER order state journal. For broker-authoritative status use connected Alpaca PAPER tools. Read-only."""
    return data.paper_orders(limit)


@mcp.tool()
def argus_fills(limit: int = 25) -> dict:
    """Read ARGUS's local broker-confirmed execution fill journal; capped and no account or order IDs. Read-only."""
    return data.fills(limit)


@mcp.tool()
def argus_backtests(limit: int = 10) -> dict:
    """List recent saved ARGUS historical backtesting runs. Does not start a backtest or place orders. Read-only."""
    return data.backtests(limit)


@mcp.tool()
def argus_research(symbol: str = "SPY") -> dict:
    """Get ARGUS's research-only daily screener indicators and VADER headline sentiment for one ticker. Read-only."""
    return data.research(symbol)


if __name__ == "__main__":
    # STDIO is deliberate: never bind a public/listening HTTP port.
    # Never print anything to stdout other than MCP protocol frames.
    mcp.run(transport="stdio")
