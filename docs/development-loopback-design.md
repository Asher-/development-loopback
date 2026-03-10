# Development Loopback — Design

## Problem

MCP servers with their own UI (Chrome extension side panel, Electron app, web dashboard) need to send chat messages to an AI during development. The two obvious approaches both fail:

- **MCP sampling** (`create_message`): Claude Code doesn't implement it — calls hang indefinitely ([#1785](https://github.com/anthropics/claude-code/issues/1785))
- **`claude -p` subprocess**: Doesn't work from within an MCP server process launched by Claude Code — nested session detection, environment conflicts
- **Direct Anthropic API**: Works, but costs real money. During development you want to use your existing Claude Code subscription (Max/Pro)

Claude Code also has no server-push mechanism — no notifications, no elicitations, no sampling. It's strictly request-response via tools and resources.

## Solution

A standalone MCP server that acts as a **chat bridge**. It listens on a TCP port for external clients and exposes two MCP tools so Claude Code can participate in a conversation loop by polling.

## Architecture

```
Chrome extension / web panel / any UI client
    │
    │  TCP (newline-delimited JSON)
    ▼
Bridge Server (port 9100)
    │
    │  asyncio.Queue connects TCP listener to MCP tools
    ▼
Claude Code (MCP client)
    │
    │  calls check_messages() in a loop
    │  processes message, uses any available tools
    │  calls send_response() to reply
    ▼
Bridge Server routes response back to the waiting TCP client
```

### Key properties

- **Claude Code IS the AI.** No API calls needed — Claude Code processes messages in its own conversation context with full tool access.
- **Poll-based.** `check_messages(timeout_ms=500)` returns quickly. Claude Code calls it in a tight loop. No server-push required.
- **Localhost only.** Development tool — no auth, no encryption, no persistence.
- **General purpose.** Not tied to any specific MCP server or UI. Any client that speaks newline-delimited JSON over TCP can use it.

## Components

### Bridge Server (`server.py`)

A single-file FastMCP server. Three responsibilities:

1. **TCP listener** on configurable port (default 9100). Accepts connections, reads newline-delimited JSON, queues incoming messages.
2. **MCP tool `check_messages`** — pops the next message from the queue (with 500ms timeout). Returns the message or `{"status": "no_messages"}`.
3. **MCP tool `send_response`** — looks up the conversation by ID and pushes the response to the waiting client.

### Internal state

- **Incoming queue:** `asyncio.Queue` — TCP listener pushes, `check_messages` pops.
- **Response routing:** `dict[str, asyncio.Queue]` — keyed by `conversation_id`. Each TCP client connection creates an entry; `send_response` pushes to the right queue; the TCP handler awaits the response and sends it back to the client.

### Wire protocol (TCP client <-> bridge)

Newline-delimited JSON. Two message types:

```json
// Client -> Bridge (chat request)
{"type": "chat", "conversation_id": "conv_123", "text": "How do I add a fillet?", "context": {"documentId": "abc"}}

// Bridge -> Client (chat response)
{"type": "response", "conversation_id": "conv_123", "text": "To add a fillet, select the edge..."}
```

The `context` field is opaque — the bridge passes it through to Claude Code, which can use it however it wants.

### MCP tools

```python
@mcp.tool()
async def check_messages(timeout_ms: int = 500) -> dict:
    """Check for pending chat messages from connected clients.

    Call this in a loop to monitor the bridge for incoming messages.
    Returns the next message or {"status": "no_messages"} after timeout.
    """

@mcp.tool()
async def send_response(conversation_id: str, text: str) -> dict:
    """Send a response back to the client that sent a message.

    The conversation_id must match the one from the received message.
    """
```

### Conversation loop

Claude Code's system prompt (or a skill) instructs it to poll:

> You are monitoring the development loopback bridge. Call `check_messages()` repeatedly. When a message arrives, process it using your available tools and knowledge, then call `send_response()` with your answer. Resume polling.

## Client integration

Any MCP server UI that wants to use the loopback in dev mode:

1. Check if the bridge is running (try connecting to port 9100)
2. If available: send chat messages over TCP, await responses
3. If not available: fall back to direct Anthropic API (production path)

This is a per-client decision. The bridge itself doesn't know or care what the client is.

## What it doesn't do

- **No Anthropic API key** — Claude Code is the AI
- **No auth/encryption** — localhost only, development use
- **No message persistence** — in-memory queues, ephemeral
- **No tool forwarding** — Claude Code uses its own tools directly
- **No streaming** — responses are delivered as complete text (streaming could be added later via chunked responses)

## File structure

```
development_loopback/
  server.py           # The bridge server (FastMCP + TCP listener)
  pyproject.toml      # Package metadata, dependencies (fastmcp, anyio)
  README.md           # Usage instructions
```

Single file. ~150 lines.
