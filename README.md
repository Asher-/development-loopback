# Development Loopback

A standalone MCP server that acts as a chat bridge between external UI clients (Chrome extensions, web panels, Electron apps) and a running Claude Code session. During development, this lets your UI chat through Claude Code's subscription instead of calling the Anthropic API directly.

## Install

```bash
pip install -e .
```

## Configure in Claude Code

Add to your `.claude.json` (or project `.mcp.json`):

```json
{
  "mcpServers": {
    "development-loopback": {
      "command": "development-loopback",
      "type": "stdio"
    }
  }
}
```

## How It Works

```
Chrome extension / web panel / any UI client
|                        |                       |
| TCP (port 9100)        | WebSocket (port 9101) |
| newline-delimited JSON | JSON text frames      |
    v                               v
Bridge Server (shared state)
    |
    |  asyncio.Queue connects listeners to MCP tools
    v
Claude Code (MCP client)
    |
    |  calls check_messages() in a loop
    |  processes message, uses any available tools
    |  calls send_response() to reply
    v
Bridge Server routes response back to the waiting client
```

Claude Code polls `check_messages(timeout_ms=500)` in a tight loop. When a message arrives, it processes the request using its full context and tool access, then calls `send_response()` to deliver the reply back to the client. Both TCP and WebSocket transports share the same bridge state -- the MCP tools are transport-agnostic.

## Wire Protocol

Same JSON format over both transports:

- **TCP** (port 9100): Newline-delimited JSON
- **WebSocket** (port 9101): JSON text frames (one message per frame)

**Client -> Bridge (chat request):**

```json
{"type": "chat", "conversation_id": "conv_123", "text": "How do I add a fillet?", "context": {"documentId": "abc"}}
```

**Bridge -> Client (response):**

```json
{"type": "response", "conversation_id": "conv_123", "text": "To add a fillet, select the edge..."}
```

The `context` field is opaque -- the bridge passes it through to Claude Code, which can use it however it wants.

## Example: Python Client

```python
import asyncio
import json
import uuid


async def chat(text: str, context: dict | None = None) -> str:
    reader, writer = await asyncio.open_connection("127.0.0.1", 9100)

    msg = {
        "type": "chat",
        "conversation_id": str(uuid.uuid4()),
        "text": text,
        "context": context or {},
    }
    writer.write(json.dumps(msg).encode() + b"\n")
    await writer.drain()

    response_line = await reader.readline()
    response = json.loads(response_line.decode().strip())

    writer.close()
    await writer.wait_closed()

    return response["text"]


# Usage:
# result = asyncio.run(chat("What tools are available?"))
```

## Example: JavaScript Client (WebSocket)

```javascript
const ws = new WebSocket("ws://127.0.0.1:9101");

ws.onopen = () => {
  ws.send(JSON.stringify({
    type: "chat",
    conversation_id: crypto.randomUUID(),
    text: "What tools are available?",
    context: {},
  }));
};

ws.onmessage = (event) => {
  const response = JSON.parse(event.data);
  console.log(response.text);
};
```

## MCP Tools

| Tool             | Description                                                                                     |
| ---------------- | ----------------------------------------------------------------------------------------------- |
| `check_messages` | Poll for pending messages (500ms timeout). Returns next message or `{"status": "no_messages"}`. |
| `send_response`  | Send a response back to the client by `conversation_id`.                                        |

## Development

```bash
# Run tests
pip install -e ".[dev]"
pytest test_server.py -v
```
