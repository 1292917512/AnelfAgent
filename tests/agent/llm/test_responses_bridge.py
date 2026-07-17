"""chat_protocol 与 responses 桥接测试。"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from agent.llm.llm_client import LLMClient, LLMClientConfig
from agent.llm.protocol import ChatProtocol
from agent.llm.responses.types import ResponseResult, ResponseUsage


def test_chat_protocol_validation() -> None:
    with pytest.raises(ValueError, match="chat_protocol"):
        LLMClientConfig(chat_protocol="websocket")
    cfg = LLMClientConfig(chat_protocol="auto", api_type="openai")
    client = LLMClient(cfg)
    assert client.resolved_chat_protocol == ChatProtocol.RESPONSES


@pytest.mark.asyncio
async def test_chat_bridges_to_responses_when_configured() -> None:
    client = LLMClient(LLMClientConfig(
        model="gpt-4o",
        api_type="openai",
        base_url="https://api.openai.com/v1",
        chat_protocol="responses",
    ))
    client.responses_create = AsyncMock(return_value=ResponseResult(  # type: ignore[method-assign]
        id="resp_x",
        status="completed",
        model="gpt-4o",
        output_text="bridged",
        usage=ResponseUsage(input_tokens=1, output_tokens=1, total_tokens=2),
    ))

    result = await client.chat([{"role": "user", "content": "hi"}])
    assert result.content == "bridged"
    client.responses_create.assert_awaited_once()
    kwargs: dict[str, Any] = client.responses_create.await_args.kwargs
    assert kwargs["input"] == "hi"
