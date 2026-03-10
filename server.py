"""Development loopback bridge.

A standalone MCP server that acts as a chat bridge between external UI
clients (Chrome extensions, web panels, etc.) and a running Claude Code
session.  External clients connect over TCP (newline-delimited JSON,
port 9100) or WebSocket (JSON text frames, port 9101); Claude Code
polls via MCP tools.

Usage:
    # As MCP server (stdio transport, for Claude Code):
    python server.py

    # Or via the installed entry point:
    development-loopback
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import websockets
from mcp.server.fastmcp import FastMCP

logger = logging.getLogger(__name__)

BRIDGE_PORT = 9100
WS_PORT = 9101

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


async def _handle_tcp_client(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
) -> None:
    """Handle a single TCP client connection.

    Reads newline-delimited JSON messages.  For each "chat" message:
    1. Creates a response queue for the conversation_id.
    2. Pushes the message onto the incoming queue.
    3. Awaits the response from the response queue.
    4. Sends the response back to the client as newline-delimited JSON.
    """
    peer = writer.get_extra_info("peername", ("unknown",))
    logger.info("TCP client connected: %s", peer)

    try:
        while True:
            line = await reader.readline()
            if not line:
                break

            line_str = line.decode().strip()
            if not line_str:
                continue

            try:
                data = json.loads(line_str)
            except json.JSONDecodeError:
                error = {"type": "error", "error": "Invalid JSON"}
                writer.write(json.dumps(error).encode() + b"\n")
                await writer.drain()
                continue

            if data.get("type") != "chat":
                error = {"type": "error", "error": f"Unknown type: {data.get('type')}"}
                writer.write(json.dumps(error).encode() + b"\n")
                await writer.drain()
                continue

            conv_id = data.get("conversation_id", "")
            if not conv_id:
                error = {"type": "error", "error": "Missing conversation_id"}
                writer.write(json.dumps(error).encode() + b"\n")
                await writer.drain()
                continue

            # Create response queue and enqueue for Claude Code.
            response_q: asyncio.Queue[str] = asyncio.Queue()
            _response_queues[conv_id] = response_q

            await _incoming.put({
                "conversation_id": conv_id,
                "text": data.get("text", ""),
                "context": data.get("context", {}),
            })

            # Wait for Claude Code to respond.
            try:
                response_text = await response_q.get()
            finally:
                _response_queues.pop(conv_id, None)

            response = {
                "type": "response",
                "conversation_id": conv_id,
                "text": response_text,
            }
            writer.write(json.dumps(response).encode() + b"\n")
            await writer.drain()

    except (ConnectionResetError, BrokenPipeError):
        logger.info("TCP client disconnected: %s", peer)
    except Exception:
        logger.exception("Error handling TCP client %s", peer)
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass


async def _start_tcp_listener(port: int = BRIDGE_PORT) -> asyncio.Server:
    """Start the TCP listener for external clients."""
    server = await asyncio.start_server(
        _handle_tcp_client,
        host="127.0.0.1",
        port=port,
    )
    logger.info("TCP bridge listening on 127.0.0.1:%d", port)
    return server


async def _handle_ws_client(websocket: Any) -> None:
    """Handle a single WebSocket client connection.

    Same logic as _handle_tcp_client but reads/writes WebSocket frames
    instead of newline-delimited bytes.
    """
    peer = websocket.remote_address
    logger.info("WebSocket client connected: %s", peer)

    try:
        async for raw_message in websocket:
            try:
                data = json.loads(raw_message)
            except json.JSONDecodeError:
                await websocket.send(json.dumps({"type": "error", "error": "Invalid JSON"}))
                continue

            if data.get("type") != "chat":
                await websocket.send(json.dumps({"type": "error", "error": f"Unknown type: {data.get('type')}"}))
                continue

            conv_id = data.get("conversation_id", "")
            if not conv_id:
                await websocket.send(json.dumps({"type": "error", "error": "Missing conversation_id"}))
                continue

            # Create response queue and enqueue for Claude Code.
            response_q: asyncio.Queue[str] = asyncio.Queue()
            _response_queues[conv_id] = response_q

            await _incoming.put({
                "conversation_id": conv_id,
                "text": data.get("text", ""),
                "context": data.get("context", {}),
            })

            # Wait for Claude Code to respond.
            try:
                response_text = await response_q.get()
            finally:
                _response_queues.pop(conv_id, None)

            await websocket.send(json.dumps({
                "type": "response",
                "conversation_id": conv_id,
                "text": response_text,
            }))

    except websockets.ConnectionClosed:
        logger.info("WebSocket client disconnected: %s", peer)
    except Exception:
        logger.exception("Error handling WebSocket client %s", peer)


async def _start_ws_listener(port: int = WS_PORT) -> Any:
    """Start the WebSocket listener for browser-based clients."""
    ws_server = await websockets.serve(
        _handle_ws_client,
        "127.0.0.1",
        port,
    )
    logger.info("WebSocket bridge listening on 127.0.0.1:%d", port)
    return ws_server


def main():
    import anyio

    async def _run():
        tcp_server = await _start_tcp_listener()
        ws_server = await _start_ws_listener()
        try:
            await mcp.run_stdio_async()
        finally:
            tcp_server.close()
            await tcp_server.wait_closed()
            ws_server.close()
            await ws_server.wait_closed()

    anyio.run(_run)


if __name__ == "__main__":
    main()
