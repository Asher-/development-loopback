"""Tests for the development loopback bridge."""

import asyncio
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
