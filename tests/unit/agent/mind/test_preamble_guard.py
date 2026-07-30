"""前言守卫（preamble guard）单元测试。

针对"不爱用工具、频繁直接输出文本"的模型：
- 承诺式过渡文本（"我来看看…"）被拦截，不作为最终回复投递
- 被拦前言在终态投递时合并进最终回复（不丢表达、不轰炸）
- 拦截上限 2 次，第 3 次放行（防死循环）
- looks_like_preamble 的模式边界
"""

from __future__ import annotations

import time
from types import SimpleNamespace
from typing import List
from unittest.mock import AsyncMock

import pytest

from agent.channel.reply_route import is_short_ack, looks_like_preamble
from agent.messages.presets import MessageUser
from agent.mind.tools.round_helpers import ThinkMode
from agent.mind.tools.think_loop import think_loop


class TestLooksLikePreamble:
    @pytest.mark.parametrize("text", [
        "好的，我来看看。",
        "我帮你查一下相关资料。",
        "让我检查一下这个文件",
        "我现在就去处理",
        "稍等，我查查看",
        "Let me check that for you",
        "I'll look into it",
        "Sure, let me search the web",
    ])
    def test_preamble_detected(self, text: str) -> None:
        assert looks_like_preamble(text)

    @pytest.mark.parametrize("text", [
        "查到了：答案是 42。",
        "今天天气不错，适合出门。",
        "我已经完成了所有修改，文件已保存。",
        "看这个结果，说明配置生效了。",  # 非承诺式开头
        "",
    ])
    def test_normal_text_not_detected(self, text: str) -> None:
        assert not looks_like_preamble(text)

    def test_long_text_not_detected(self) -> None:
        """长文本即使以承诺式开头也往往自带结论，不误拦。"""
        text = "我来看看。" + "详细分析内容。" * 30
        assert not looks_like_preamble(text)


class TestIsShortAck:
    def test_short_ack(self) -> None:
        assert is_short_ack("已发送 ✅")
        assert is_short_ack("")

    def test_substantial_text(self) -> None:
        assert not is_short_ack(
            "这是一段超过四十字的实质性总结内容，包含具体的信息、完整的分析过程和明确的最终结论。"
        )


# ------------------------------------------------------------------
# think_loop 级：替身 Mind
# ------------------------------------------------------------------

class _PreamblePfc:
    def build_execution_context(self, *a, **kw) -> dict:
        return {"role": "system", "content": "exec"}

    def add_temporary(self, clip) -> None:
        pass

    def clear_dynamic_tools(self) -> None:
        pass

    def record_tool_use(self, name: str) -> None:
        pass

    def expand_discovered_tools(self, tool_calls) -> None:
        pass

    def consume_scope_task(self, scope) -> bool:
        return True

    def collect_images(self, scope: str = "") -> list:
        return []

    def collect_media(self, scope: str = "") -> list:
        return []

    def activate_media_tools(self, images: list, media_segments: list) -> None:
        pass

    async def get_active_tool_schemas(self, adapter_key: str = "", scope: str = "") -> list:
        return []


class _ScriptedMind:
    """按脚本依次返回 LLM 结果的 Mind 替身。"""

    def __init__(self, script: List[str]) -> None:
        self.pfc = _PreamblePfc()
        self.compressor = None
        self.conversation_data = SimpleNamespace(
            get_fetch_watermark=lambda st, sid: None,
        )
        self._add_system_context = AsyncMock()
        self._script = list(script)
        self.calls = 0

    def _resolve_adapter_key(self) -> str:
        return "test"

    @staticmethod
    def _resolve_scope(anything) -> tuple[str, str]:
        return anything.scope_type, anything.scope_id

    @staticmethod
    def _resolve_entity_scope(anything) -> str:
        return anything.entity_scope if anything else ""

    @property
    def tool_executor(self):
        async def _exec(tc) -> str:
            return '{"ok": true}'
        return _exec

    def _set_phase(self, phase) -> None:
        pass

    def _get_mind_config(self):
        return SimpleNamespace(llm_timeout=10.0, force_tool_use=False)

    def get_model_context_length(self) -> int:
        return 0

    async def _invoke_llm_unified(self, messages, tools, anything=None, *, tool_choice=None, options=None):
        content = self._script[min(self.calls, len(self._script) - 1)]
        self.calls += 1
        return SimpleNamespace(
            content=content, tool_calls=[], reasoning_content="",
            usage=None, raw=None, model="fake",
        )


def _run_loop(mind) -> list[str]:
    steps: list[str] = []
    import asyncio

    async def _go() -> None:
        await think_loop(
            mind,
            mode=ThinkMode.REPLY,
            tool_chain=[],
            execution_steps=steps,
            start_time=time.time(),
            safety_limit=8,
            collected_text=[],
            active_tools=[],
            anything=MessageUser(uid=1, adapter_key="test"),
            base_messages=[{"role": "user", "content": "帮我查一下"}],
        )

    asyncio.get_event_loop().run_until_complete(_go())
    return steps


class TestPreambleGuard:
    async def test_preamble_intercepted_then_merged(self, monkeypatch) -> None:
        """前言被拦截不投递；最终回复合并前言一次投递。"""
        delivered: list[str] = []

        async def _fake_deliver(target, content) -> bool:
            delivered.append(content)
            return True

        monkeypatch.setattr("agent.mind.tools.think_loop.deliver_text", _fake_deliver)
        # plan 守卫替身：无活跃计划
        monkeypatch.setattr(
            "agent.planning.tracker.guard_feedback_for_text_only",
            AsyncMock(return_value=""),
        )

        mind = _ScriptedMind(["好的，我来帮你查一下。", "查到了：答案是 42。"])
        steps: list[str] = []
        await think_loop(
            mind,
            mode=ThinkMode.REPLY,
            tool_chain=[],
            execution_steps=steps,
            start_time=time.time(),
            safety_limit=8,
            collected_text=[],
            active_tools=[],
            anything=MessageUser(uid=1, adapter_key="test"),
            base_messages=[{"role": "user", "content": "帮我查一下"}],
        )

        assert len(delivered) == 1, "前言不应单独投递"
        assert "我来帮你查一下" in delivered[0]
        assert "答案是 42" in delivered[0]
        assert any("预告文本被拦截" in s for s in steps)

    async def test_preamble_guard_limit(self, monkeypatch) -> None:
        """连续前言达到上限后放行投递（防死循环）。"""
        delivered: list[str] = []

        async def _fake_deliver(target, content) -> bool:
            delivered.append(content)
            return True

        monkeypatch.setattr("agent.mind.tools.think_loop.deliver_text", _fake_deliver)
        monkeypatch.setattr(
            "agent.planning.tracker.guard_feedback_for_text_only",
            AsyncMock(return_value=""),
        )

        mind = _ScriptedMind(["我来看看", "让我查查", "我去找找"])
        await think_loop(
            mind,
            mode=ThinkMode.REPLY,
            tool_chain=[],
            execution_steps=[],
            start_time=time.time(),
            safety_limit=8,
            collected_text=[],
            active_tools=[],
            anything=MessageUser(uid=1, adapter_key="test"),
            base_messages=[{"role": "user", "content": "hi"}],
        )

        assert len(delivered) == 1, "上限后应放行投递并结束"
        assert "我来看看" in delivered[0] and "让我查查" in delivered[0]

    async def test_normal_text_delivered_directly(self, monkeypatch) -> None:
        """正常文本不触发前言守卫，直接投递。"""
        delivered: list[str] = []

        async def _fake_deliver(target, content) -> bool:
            delivered.append(content)
            return True

        monkeypatch.setattr("agent.mind.tools.think_loop.deliver_text", _fake_deliver)
        monkeypatch.setattr(
            "agent.planning.tracker.guard_feedback_for_text_only",
            AsyncMock(return_value=""),
        )

        mind = _ScriptedMind(["今天天气不错。"])
        await think_loop(
            mind,
            mode=ThinkMode.REPLY,
            tool_chain=[],
            execution_steps=[],
            start_time=time.time(),
            safety_limit=8,
            collected_text=[],
            active_tools=[],
            anything=MessageUser(uid=1, adapter_key="test"),
            base_messages=[{"role": "user", "content": "hi"}],
        )

        assert delivered == ["今天天气不错。"]
