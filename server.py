"""Development loopback bridge.

A standalone MCP server that acts as a chat bridge between external UI
clients (Chrome extensions, web panels, etc.) and a running Claude Code
session.  External clients connect over TCP (newline-delimited JSON);
Claude Code polls via MCP tools.

Usage:
    # As MCP server (stdio transport, for Claude Code):
    python server.py

    # Or via the installed entry point:
    development-loopback
"""

import asyncio
import json
import logging

from mcp.server.fastmcp import FastMCP

logger = logging.getLogger(__name__)

BRIDGE_PORT = 9100

mcp = FastMCP(
    name="development-loopback",
)

# -- Bridge state --
_incoming: asyncio.Queue[dict] = asyncio.Queue()
_response_queues: dict[str, asyncio.Queue[str]] = {}


def _reset_bridge_state() -> None:
    """Reset bridge queues.  Used by tests to avoid cross-loop binding."""
    global _incoming
    _incoming = asyncio.Queue()
    _response_queues.clear()


@mcp.tool()
async def check_messages(timeout_ms: int = 500) -> dict:
    """Check for pending chat messages from connected clients.

    Call this in a loop to monitor the bridge for incoming messages.
    Returns the next message or {"status": "no_messages"} after timeout.

    Args:
        timeout_ms: How long to wait for a message (default 500ms).
    """
    try:
        msg = await asyncio.wait_for(
            _incoming.get(),
            timeout=timeout_ms / 1000.0,
        )
        return {
            "status": "message",
            "conversation_id": msg["conversation_id"],
            "text": msg["text"],
            "context": msg.get("context", {}),
        }
    except asyncio.TimeoutError:
        return {"status": "no_messages"}


@mcp.tool()
async def send_response(conversation_id: str, text: str) -> dict:
    """Send a response back to the client that sent a message.

    The conversation_id must match one from a received message.

    Args:
        conversation_id: The conversation to respond to.
        text: The response text.
    """
    q = _response_queues.get(conversation_id)
    if q is None:
        return {
            "status": "error",
            "error": f"Unknown conversation_id: {conversation_id}",
        }
    await q.put(text)
    return {"status": "sent", "conversation_id": conversation_id}


def main():
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
