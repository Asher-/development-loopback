"""Tests for the development loopback bridge."""

import asyncio
import json
import pytest
import server
from server import check_messages, send_response, _reset_bridge_state


@pytest.fixture(autouse=True)
def _fresh_bridge_state():
    """Reset bridge queues before each test to avoid cross-loop binding."""
    _reset_bridge_state()


@pytest.mark.asyncio
async def test_incoming_queue_exists():
    assert isinstance(server._incoming, asyncio.Queue)


@pytest.mark.asyncio
async def test_response_queues_is_dict():
    assert isinstance(server._response_queues, dict)


@pytest.mark.asyncio
async def test_check_messages_no_messages():
    result = await check_messages(timeout_ms=50)
    assert result == {"status": "no_messages"}


@pytest.mark.asyncio
async def test_check_messages_receives_message():
    msg = {"conversation_id": "test_1", "text": "hello", "context": {}}
    await server._incoming.put(msg)
    result = await check_messages(timeout_ms=500)
    assert result["status"] == "message"
    assert result["conversation_id"] == "test_1"
    assert result["text"] == "hello"


@pytest.mark.asyncio
async def test_check_messages_timeout_is_respected():
    import time
    start = time.monotonic()
    await check_messages(timeout_ms=100)
    elapsed = time.monotonic() - start
    assert elapsed < 0.5


@pytest.mark.asyncio
async def test_send_response_delivers():
    q: asyncio.Queue[str] = asyncio.Queue()
    server._response_queues["conv_42"] = q
    result = await send_response("conv_42", "here is your answer")
    assert result["status"] == "sent"
    response_text = await q.get()
    assert response_text == "here is your answer"
    del server._response_queues["conv_42"]


@pytest.mark.asyncio
async def test_send_response_unknown_conversation():
    result = await send_response("nonexistent", "hello")
    assert result["status"] == "error"


# -- Task 5: TCP client handler --

from server import _handle_tcp_client


@pytest.mark.asyncio
async def test_tcp_client_roundtrip():
    """Simulate a TCP client: send a chat message, receive a response."""
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
    incoming_msg = await asyncio.wait_for(server._incoming.get(), timeout=1.0)
    assert incoming_msg["conversation_id"] == "tcp_1"
    assert incoming_msg["text"] == "test message"

    # Simulate Claude Code sending a response.
    assert "tcp_1" in server._response_queues
    await server._response_queues["tcp_1"].put("response text")

    # Wait for handler to finish (it should send the response and exit
    # because we fed EOF).
    await asyncio.wait_for(handler_task, timeout=2.0)

    # Check the response was written.
    response_line = output_buf.decode().strip()
    response = json.loads(response_line)
    assert response["type"] == "response"
    assert response["conversation_id"] == "tcp_1"
    assert response["text"] == "response text"
