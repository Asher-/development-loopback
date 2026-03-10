# WebSocket Transport — Design

## Problem

The development loopback bridge currently only accepts TCP connections (newline-delimited JSON on port 9100). Browser-based clients like Chrome extensions cannot open raw TCP sockets — they need WebSocket.

## Solution

Add a WebSocket server on port 9101 alongside the existing TCP server. Both transports feed the same `_incoming` queue and `_response_queues`. The MCP tools (`check_messages`, `send_response`) and bridge state are unchanged.

## Architecture

```
Chrome extension / web panel / any browser client
    |
    |  WebSocket (JSON text frames, port 9101)
    v
Bridge Server
    |                    Python scripts / CLI tools
    |                        |
    |                        |  TCP (newline-delimited JSON, port 9100)
    |                        v
    +--- shared bridge state (asyncio.Queue, response routing dict)
    |
    v
Claude Code (MCP client)
    calls check_messages() / send_response()
```

## New components

### `_handle_ws_client(websocket)`

Structurally identical to `_handle_tcp_client`. For each incoming WebSocket text frame:

1. Parse JSON, validate (type must be "chat", conversation_id required)
2. Create response queue for conversation_id
3. Push message onto `_incoming`
4. Await response from response queue
5. Send response as WebSocket text frame

Error responses sent as JSON frames with `{"type": "error", "error": "..."}`.

### `_start_ws_listener(port=WS_PORT)`

Uses `websockets.serve()` on `127.0.0.1`. Returns the server object.

### Updated `main()`

Starts TCP, WS, and MCP stdio transport. Tears down both listeners in `finally`.

## Wire protocol (WebSocket)

Same JSON format as TCP. Each message is one WebSocket text frame (no newline delimiting needed).

```json
// Client -> Bridge
{"type": "chat", "conversation_id": "conv_123", "text": "hello", "context": {}}

// Bridge -> Client
{"type": "response", "conversation_id": "conv_123", "text": "hi there"}

// Bridge -> Client (error)
{"type": "error", "error": "Missing conversation_id"}
```

## Constants

```python
BRIDGE_PORT = 9100   # TCP (existing)
WS_PORT = 9101       # WebSocket (new)
```

## New dependency

`websockets` added to `pyproject.toml` dependencies.

## What doesn't change

- `_incoming` queue and `_response_queues` dict — shared by both transports
- `check_messages` and `send_response` MCP tools — transport-agnostic
- `_reset_bridge_state()` — still resets everything for tests
- TCP transport — stays exactly as-is
