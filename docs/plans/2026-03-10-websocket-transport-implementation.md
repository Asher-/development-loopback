# WebSocket Transport Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a WebSocket server (port 9101) alongside the existing TCP server so browser-based clients (Chrome extensions) can connect to the development loopback bridge.

**Architecture:** A `_handle_ws_client` function reads JSON from WebSocket text frames, feeds the same `_incoming` queue and `_response_queues` as TCP. A `_start_ws_listener` starts the server. `main()` starts both listeners.

**Tech Stack:** Python 3.11+, `websockets` library, FastMCP, asyncio

---

### Task 1: Add `websockets` dependency

**Files:**
- Modify: `pyproject.toml`

**Step 1: Add dependency**

Add `"websockets>=13.0"` to the dependencies list in `pyproject.toml`:

```toml
dependencies = [
    "mcp[cli]>=1.0.0",
    "websockets>=13.0",
]
```

**Step 2: Install**

Run: `cd /Users/asher/Dropbox/Projects/claude/development_loopback && .venv/bin/pip install -e .`
Expected: Successfully installs websockets.

**Step 3: Verify import works**

Run: `.venv/bin/python -c "import websockets; print(websockets.__version__)"`
Expected: Prints version number.

**Step 4: Commit**

```bash
git add pyproject.toml
git commit -m "feat: add websockets dependency"
```

---

### Task 2: WebSocket client handler and listener

**Files:**
- Modify: `server.py`
- Modify: `test_server.py`

**Step 1: Add WS_PORT constant to `server.py`**

After the existing `BRIDGE_PORT = 9100` line, add:

```python
WS_PORT = 9101
```

**Step 2: Write the failing test for WS handler**

Append to `test_server.py`:

```python
# -- WebSocket transport --

import websockets

from server import _start_ws_listener


@pytest.mark.asyncio
async def test_ws_client_roundtrip():
    """Full roundtrip: WS send -> check_messages -> send_response -> WS receive."""
    import socket

    # Find a free port.
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]

    ws_server = await _start_ws_listener(port=port)

    try:
        async with websockets.connect(f"ws://127.0.0.1:{port}") as ws:
            # Send a chat message.
            msg = {"type": "chat", "conversation_id": "ws_1", "text": "hello from ws", "context": {}}
            await ws.send(json.dumps(msg))

            # Poll for the message via MCP tool.
            result = await check_messages(timeout_ms=2000)
            assert result["status"] == "message"
            assert result["conversation_id"] == "ws_1"
            assert result["text"] == "hello from ws"

            # Respond via MCP tool.
            send_result = await send_response("ws_1", "ws response")
            assert send_result["status"] == "sent"

            # Read response from WebSocket.
            response_raw = await asyncio.wait_for(ws.recv(), timeout=2.0)
            response = json.loads(response_raw)
            assert response["type"] == "response"
            assert response["conversation_id"] == "ws_1"
            assert response["text"] == "ws response"

    finally:
        ws_server.close()
        await ws_server.wait_closed()
```

**Step 3: Run test to verify it fails**

Run: `.venv/bin/pytest test_server.py::test_ws_client_roundtrip -v`
Expected: FAIL — `_start_ws_listener` not defined.

**Step 4: Implement `_handle_ws_client` and `_start_ws_listener`**

Add to `server.py` after `_start_tcp_listener` and before `main()`:

```python
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
```

Also add `from typing import Any` at the top of `server.py` if not already present, and add `import websockets` after the existing imports.

**Step 5: Run all tests**

Run: `.venv/bin/pytest test_server.py -v`
Expected: All PASS (11 tests).

**Step 6: Commit**

```bash
git add server.py test_server.py
git commit -m "feat: add WebSocket transport (handler + listener)"
```

---

### Task 3: Wire WS listener into `main()` and update docs

**Files:**
- Modify: `server.py`
- Modify: `README.md`

**Step 1: Update `main()` to start both listeners**

Replace the existing `main()` in `server.py`:

```python
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
```

**Step 2: Run all tests**

Run: `.venv/bin/pytest test_server.py -v`
Expected: All PASS.

**Step 3: Update README.md**

Add a WebSocket section to the "How It Works" diagram and add a JavaScript client example. Update the architecture diagram to show both ports. Add `WS_PORT = 9101` to the docs.

**Step 4: Update skill and design doc references**

Update `/Users/asher/Dropbox/Projects/claude/skills/claude-code-loopback/SKILL.md` to mention WebSocket as an available transport.

**Step 5: Commit**

```bash
git add server.py README.md
git commit -m "feat: wire WebSocket listener into main, update docs"
```

**Step 6: Push and update submodule pointer**

```bash
cd /Users/asher/Dropbox/Projects/claude/development_loopback
git push origin main

cd /Users/asher/Dropbox/Projects/claude
git add development_loopback
git commit -m "chore: update development-loopback submodule (WebSocket transport)"
git push origin main
```
