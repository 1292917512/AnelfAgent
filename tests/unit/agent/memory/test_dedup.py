"""记忆去重裁决 light_llm 调用行为单元测试（流式通道 + 思考档位配置）。"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from agent.memory import dedup


class _FakeManager:
    def __init__(self) -> None:
        self.kwargs: dict[str, Any] = {}

    async def chat_with_fallback(self, messages: list[dict], **kwargs: Any) -> Any:
        self.kwargs = kwargs
        self.messages = messages
        return SimpleNamespace(content='{"action": "store"}')


@pytest.mark.asyncio
async def test_light_llm_uses_stream_channel(monkeypatch: pytest.MonkeyPatch) -> None:
    """裁决调用走流式通道：空闲判死（思考中不掐断），长思考模型不被墙钟截断。"""
    manager = _FakeManager()
    monkeypatch.setattr("agent.llm.get_llm_manager", lambda: manager)
    monkeypatch.setattr(dedup, "get_config", lambda key, default=None: default)

    result = await dedup.light_llm("裁决提示词")

    assert result == '{"action": "store"}'
    assert manager.kwargs["stream"] is True
    assert manager.kwargs["timeout"] == 120.0
    assert manager.kwargs["options"] == {"temperature": 0.1}
    assert manager.messages == [{"role": "user", "content": "裁决提示词"}]


@pytest.mark.asyncio
async def test_light_llm_applies_judge_reasoning_effort(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """memory_judge_reasoning_effort 配置生效时注入 per-call 思考档位。"""
    manager = _FakeManager()
    monkeypatch.setattr("agent.llm.get_llm_manager", lambda: manager)
    monkeypatch.setattr(
        dedup, "get_config",
        lambda key, default=None: "low" if key == "memory_judge_reasoning_effort" else default,
    )

    await dedup.light_llm("p", temperature=0.2)

    assert manager.kwargs["options"] == {"temperature": 0.2, "reasoning_effort": "low"}


@pytest.mark.asyncio
async def test_light_llm_ignores_invalid_effort(monkeypatch: pytest.MonkeyPatch) -> None:
    """非法思考档位被 normalize_effort 归一为空，不注入 options。"""
    manager = _FakeManager()
    monkeypatch.setattr("agent.llm.get_llm_manager", lambda: manager)
    monkeypatch.setattr(
        dedup, "get_config",
        lambda key, default=None: "bogus" if key == "memory_judge_reasoning_effort" else default,
    )

    await dedup.light_llm("p")

    assert "reasoning_effort" not in manager.kwargs["options"]
