# Development Loopback Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build a standalone MCP chat bridge that lets external UI clients route chat through a running Claude Code session during development.

**Architecture:** A single-file FastMCP server with an async TCP listener. Incoming messages queue via `asyncio.Queue`; responses route back via per-conversation queues. Two MCP tools (`check_messages`, `send_response`) let Claude Code poll and reply.

**Tech Stack:** Python 3.11+, FastMCP, anyio/asyncio

---

### Task 1: Project scaffolding

**Files:**
- Create: `pyproject.toml`
- Create: `server.py`

**Step 1: Create `pyproject.toml`**

```toml
[project]
name = "development-loopback"
version = "0.1.0"
description = "Development chat bridge — routes UI chat through a running Claude Code session"
requires-python = ">=3.11"
dependencies = [
    "mcp[cli]>=1.0.0",
]

[project.scripts]
development-loopback = "server:main"
```

**Step 2: Create empty `server.py` with imports and main**

```python
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


def main():
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
```

**Step 3: Verify it runs**

Run: `cd /Users/asher/Dropbox/Projects/claude/development_loopback && python server.py`
Expected: Process starts, waits for stdio input, exits cleanly on Ctrl-C.

**Step 4: Commit**

```bash
git add pyproject.toml server.py
git commit -m "feat: project scaffolding — FastMCP server skeleton"
```

---

### Task 2: Internal message queues

**Files:**
- Modify: `server.py`

**Step 1: Write the failing test**

Create `test_server.py`:

```python
"""Tests for the development loopback bridge."""

import asyncio
import pytest
from server import _incoming, _response_queues


@pytest.mark.asyncio
async def test_incoming_queue_exists():
    """Incoming queue is an asyncio.Queue."""
    assert isinstance(_incoming, asyncio.Queue)


@pytest.mark.asyncio
async def test_response_queues_is_dict():
    """Response routing dict exists."""
    assert isinstance(_response_queues, dict)
```

**Step 2: Run test to verify it fails**

Run: `pytest test_server.py -v`
Expected: FAIL with `ImportError` — `_incoming` and `_response_queues` not defined.

**Step 3: Add queue state to `server.py`**

Add after the `mcp = FastMCP(...)` line:

```python
# -- Bridge state --
# Incoming messages from TCP clients, consumed by check_messages tool.
_incoming: asyncio.Queue[dict] = asyncio.Queue()

# Per-conversation response queues.  TCP handler creates an entry when
# a message arrives; send_response pushes to the right queue; TCP
# handler awaits and sends the response back to the client.
_response_queues: dict[str, asyncio.Queue[str]] = {}
```

**Step 4: Run test to verify it passes**

Run: `pytest test_server.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add server.py test_server.py
git commit -m "feat: add internal message queues"
```

---

### Task 3: `check_messages` MCP tool

**Files:**
- Modify: `server.py`
- Modify: `test_server.py`

**Step 1: Write the failing test**

Append to `test_server.py`:

```python
from server import check_messages, _incoming


@pytest.mark.asyncio
async def test_check_messages_no_messages():
    """Returns no_messages when queue is empty (within timeout)."""
    result = await check_messages(timeout_ms=50)
    assert result == {"status": "no_messages"}


@pytest.mark.asyncio
async def test_check_messages_receives_message():
    """Returns a queued message."""
    msg = {
        "conversation_id": "test_1",
        "text": "hello",
        "context": {},
    }
    await _incoming.put(msg)
    result = await check_messages(timeout_ms=500)
    assert result["status"] == "message"
    assert result["conversation_id"] == "test_1"
    assert result["text"] == "hello"


@pytest.mark.asyncio
async def test_check_messages_timeout_is_respected():
    """Should return within roughly timeout_ms when no messages."""
    import time
    start = time.monotonic()
    await check_messages(timeout_ms=100)
    elapsed = time.monotonic() - start
    assert elapsed < 0.5  # generous upper bound
```

**Step 2: Run test to verify it fails**

Run: `pytest test_server.py::test_check_messages_no_messages -v`
Expected: FAIL — `check_messages` not defined.

**Step 3: Implement `check_messages`**

Add to `server.py`:

```python
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
```

**Step 4: Run all tests to verify they pass**

Run: `pytest test_server.py -v`
Expected: All PASS

**Step 5: Commit**

```bash
git add server.py test_server.py
git commit -m "feat: add check_messages MCP tool"
```

---

### Task 4: `send_response` MCP tool

**Files:**
- Modify: `server.py`
- Modify: `test_server.py`

**Step 1: Write the failing test**

Append to `test_server.py`:

```python
from server import send_response, _response_queues


@pytest.mark.asyncio
async def test_send_response_delivers():
    """send_response pushes to the right conversation queue."""
    q: asyncio.Queue[str] = asyncio.Queue()
    _response_queues["conv_42"] = q

    result = await send_response("conv_42", "here is your answer")
    assert result["status"] == "sent"

    response_text = await q.get()
    assert response_text == "here is your answer"

    # Clean up.
    del _response_queues["conv_42"]


@pytest.mark.asyncio
async def test_send_response_unknown_conversation():
    """send_response returns error for unknown conversation_id."""
    result = await send_response("nonexistent", "hello")
    assert result["status"] == "error"
```

**Step 2: Run test to verify it fails**

Run: `pytest test_server.py::test_send_response_delivers -v`
Expected: FAIL — `send_response` not defined.

**Step 3: Implement `send_response`**

Add to `server.py`:

```python
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
```

**Step 4: Run all tests**

Run: `pytest test_server.py -v`
Expected: All PASS

**Step 5: Commit**

```bash
git add server.py test_server.py
git commit -m "feat: add send_response MCP tool"
```

---

### Task 5: TCP listener

**Files:**
- Modify: `server.py`
- Modify: `test_server.py`

**Step 1: Write the failing test**

Append to `test_server.py`:

```python
from server import _handle_tcp_client, _incoming, _response_queues


@pytest.mark.asyncio
async def test_tcp_client_roundtrip():
    """Simulate a TCP client: send a chat message, receive a response."""
    # Clear state.
    while not _incoming.empty():
        _incoming.get_nowait()

    # Create a mock reader/writer pair using asyncio streams.
    client_reader = asyncio.StreamReader()
    # We need a mock writer that captures output.
    output_buf = bytearray()

    class MockWriter:
        def write(self, data: bytes):
            output_buf.extend(data)
        async def drain(self):
            pass
        def close(self):
            pass
        async def wait_closed(self):
            pass
        def get_extra_info(self, key, default=None):
            return ("127.0.0.1", 9999) if key == "peername" else default

    writer = MockWriter()

    # Feed a chat message into the reader.
    msg = {"type": "chat", "conversation_id": "tcp_1", "text": "test message", "context": {}}
    client_reader.feed_data(json.dumps(msg).encode() + b"\n")
    client_reader.feed_eof()

    # Run the handler in a task.
    handler_task = asyncio.create_task(_handle_tcp_client(client_reader, writer))

    # The message should appear in the incoming queue.
    incoming_msg = await asyncio.wait_for(_incoming.get(), timeout=1.0)
    assert incoming_msg["conversation_id"] == "tcp_1"
    assert incoming_msg["text"] == "test message"

    # Simulate Claude Code sending a response.
    assert "tcp_1" in _response_queues
    await _response_queues["tcp_1"].put("response text")

    # Wait for handler to finish (it should send the response and exit
    # because we fed EOF).
    await asyncio.wait_for(handler_task, timeout=2.0)

    # Check the response was written.
    response_line = output_buf.decode().strip()
    response = json.loads(response_line)
    assert response["type"] == "response"
    assert response["conversation_id"] == "tcp_1"
    assert response["text"] == "response text"
```

**Step 2: Run test to verify it fails**

Run: `pytest test_server.py::test_tcp_client_roundtrip -v`
Expected: FAIL — `_handle_tcp_client` not defined.

**Step 3: Implement TCP handler**

Add to `server.py`:

```python
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
```

**Step 4: Run all tests**

Run: `pytest test_server.py -v`
Expected: All PASS

**Step 5: Commit**

```bash
git add server.py test_server.py
git commit -m "feat: add TCP client handler"
```

---

### Task 6: TCP server startup with MCP server

**Files:**
- Modify: `server.py`

**Step 1: Write the failing test**

Append to `test_server.py`:

```python
from server import _start_tcp_listener


@pytest.mark.asyncio
async def test_tcp_listener_starts_and_accepts():
    """TCP listener accepts a connection on the configured port."""
    import socket

    # Find a free port.
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]

    server = await _start_tcp_listener(port=port)
    assert server is not None
    assert server.is_serving()
    server.close()
    await server.wait_closed()
```

**Step 2: Run test to verify it fails**

Run: `pytest test_server.py::test_tcp_listener_starts_and_accepts -v`
Expected: FAIL — `_start_tcp_listener` not defined.

**Step 3: Implement TCP listener startup and wire into main**

Add to `server.py`:

```python
async def _start_tcp_listener(port: int = BRIDGE_PORT) -> asyncio.Server:
    """Start the TCP listener for external clients."""
    server = await asyncio.start_server(
        _handle_tcp_client,
        host="127.0.0.1",
        port=port,
    )
    logger.info("TCP bridge listening on 127.0.0.1:%d", port)
    return server
```

Update `main()`:

```python
def main():
    import anyio

    async def _run():
        tcp_server = await _start_tcp_listener()
        try:
            await mcp.run_stdio_async()
        finally:
            tcp_server.close()
            await tcp_server.wait_closed()

    anyio.run(_run)
```

**Step 4: Run all tests**

Run: `pytest test_server.py -v`
Expected: All PASS

**Step 5: Commit**

```bash
git add server.py test_server.py
git commit -m "feat: TCP listener starts alongside MCP stdio transport"
```

---

### Task 7: End-to-end integration test

**Files:**
- Modify: `test_server.py`

**Step 1: Write end-to-end test**

This test simulates the full flow: TCP client sends a message, `check_messages` returns it, `send_response` delivers the reply, TCP client receives it.

```python
@pytest.mark.asyncio
async def test_end_to_end_flow():
    """Full roundtrip: TCP send -> check_messages -> send_response -> TCP receive."""
    import socket

    # Clear state.
    while not _incoming.empty():
        _incoming.get_nowait()

    # Find a free port and start the listener.
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]

    tcp_server = await _start_tcp_listener(port=port)

    try:
        # Connect as a TCP client.
        reader, writer = await asyncio.open_connection("127.0.0.1", port)

        # Send a chat message.
        msg = {"type": "chat", "conversation_id": "e2e_1", "text": "ping", "context": {}}
        writer.write(json.dumps(msg).encode() + b"\n")
        await writer.drain()

        # Poll for the message via MCP tool.
        result = await check_messages(timeout_ms=2000)
        assert result["status"] == "message"
        assert result["text"] == "ping"

        # Respond via MCP tool.
        send_result = await send_response("e2e_1", "pong")
        assert send_result["status"] == "sent"

        # Read response from TCP.
        response_line = await asyncio.wait_for(reader.readline(), timeout=2.0)
        response = json.loads(response_line.decode().strip())
        assert response["type"] == "response"
        assert response["text"] == "pong"

        writer.close()
        await writer.wait_closed()

    finally:
        tcp_server.close()
        await tcp_server.wait_closed()
```

**Step 2: Run it**

Run: `pytest test_server.py::test_end_to_end_flow -v`
Expected: PASS (all prior tasks already implemented the pieces)

**Step 3: Run full test suite**

Run: `pytest test_server.py -v`
Expected: All PASS

**Step 4: Commit**

```bash
git add test_server.py
git commit -m "test: add end-to-end integration test"
```

---

### Task 8: Update skill and README

**Files:**
- Modify: `README.md`
- Modify: `/Users/asher/Dropbox/Projects/claude/skills/claude-code-loopback/SKILL.md`

**Step 1: Write `README.md`**

Replace contents of `README.md` with usage instructions covering:
- What it is (1 paragraph)
- How to install (`pip install -e .`)
- How to configure in Claude Code (`.claude.json` MCP server entry pointing to `development-loopback`)
- Wire protocol (the two JSON message types)
- Example: connecting from a Python client

**Step 2: Update the `claude-code-loopback` skill**

Replace the SKILL.md content to reflect the new poll-based bridge approach instead of the old `claude -p` subprocess approach. The skill should reference the `development-loopback` package and describe the `check_messages`/`send_response` tool pattern.

**Step 3: Commit both**

```bash
git add README.md
git commit -m "docs: add README with usage instructions"

cd /Users/asher/Dropbox/Projects/claude/skills/claude-code-loopback
git add SKILL.md  # (if this is a git repo, otherwise just save)
```

---

### Task 9: Commit submodule pointer in parent repo

**Files:**
- Modify: `/Users/asher/Dropbox/Projects/claude/development_loopback` (submodule pointer)

**Step 1: Update submodule pointer and commit in parent**

```bash
cd /Users/asher/Dropbox/Projects/claude
git add development_loopback
git commit -m "feat: add development-loopback submodule"
```

**Step 2: Push both repos**

```bash
cd /Users/asher/Dropbox/Projects/claude/development_loopback
git push origin main

cd /Users/asher/Dropbox/Projects/claude
git push origin main
```
