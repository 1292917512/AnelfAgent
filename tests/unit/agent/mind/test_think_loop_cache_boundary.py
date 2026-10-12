"""think_loop 与缓存断点边界集成：链尾断点逐轮移动且不越预算。

（自 llm/test_prompt_cache.py 迁入：think_loop 集成测试归属 mind 目录。）
"""

from __future__ import annotations

from helpers.think_loop_fakes import FakeMind, run_think_loop, text_result, tool_result

from agent.llm.prompt_cache import count_breakpoints, decorate_messages
from agent.mind.message_schema import normalize_for_send
from agent.mind.tools.think_loop import _collect_provider_messages
from core.context_provider import ContextProviderRegistry, ProviderMeta


def _base() -> list:
    return [
        {"role": "system", "content": "人设", "_layer": "stable"},
        {"role": "user", "content": "昨天说到哪了", "_layer": "conversation"},
    ]


def _loop_mind() -> FakeMind:
    """第 1 轮工具调用，第 2 轮纯文本独白，第 3 轮 end_reply 收敛。"""
    return FakeMind(
        rounds=[tool_result("", ["recall"]), text_result("想起来了～"),
                tool_result("", ["end_reply"], '{"silent": true}')],
        default_text=None,
    )


class TestThinkLoopChainBreakpoint:
    async def test_async_recall_changes_only_uncached_tail(self, monkeypatch) -> None:
        text = ""
        monkeypatch.setattr(ContextProviderRegistry, "_providers", {})
        ContextProviderRegistry.register(ProviderMeta(name="memory", provide_fn=lambda scope: text))
        mind = _loop_mind()
        chain = [{"role": "assistant", "content": "查询记忆", "tool_calls": [
            {"id": "recall-1", "type": "function", "function": {"name": "recall", "arguments": "{}"}},
        ]}, {"role": "tool", "tool_call_id": "recall-1", "content": "已找到事实"}]
        prefix = [*_base(), *chain]
        empty = await _collect_provider_messages(mind, "scope")
        assert empty == []
        wire_prefix = normalize_for_send(decorate_messages(prefix, anthropic=True))
        for text in ("补充事实一", "补充事实一\n补充事实二", ""):
            tail = await _collect_provider_messages(mind, "scope")
            messages = decorate_messages([*prefix, *tail], anthropic=True)
            assert normalize_for_send(messages)[:len(prefix)] == wire_prefix
            assert all(not m.get("cache_control") for m in messages[len(prefix):])
            assert [m["content"] for m in tail] == ([text] if text else [])

    async def test_think_loop_never_marks_messages(self, anything, deliver_mock) -> None:
        """think_loop 不再触碰断点：发送给 invoker 的消息零 cache_control，
        装饰统一发生在真实 _invoke_llm_unified 内（本 fake 绕过了它）。"""
        mind = _loop_mind()
        base = _base()
        await run_think_loop(mind, anything=anything, base_messages=base)
        assert len(mind.sent_messages) == 3
        for messages in mind.sent_messages:
            assert count_breakpoints(messages) == 0
        # 共享的 base 消息不被任何装饰改写
        assert all("cache_control" not in m for m in base)

    async def test_invoker_boundary_decorates_by_layer(self, anything) -> None:
        """发送边界集成：管线输出的层标签经 decorate_messages 正确落锚点
        （模拟 _invoke_llm_unified 内的装饰调用）。"""
        mind = _loop_mind()
        base = _base()
        await run_think_loop(mind, anything=anything, base_messages=base)
        # 第 2 轮（链非空）：边界装饰 = stable 末 + 历史末 + 链尾 = 3 个
        round2 = mind.sent_messages[1]
        decorated = decorate_messages(round2, anthropic=True)
        bp = [m for m in decorated if m.get("cache_control")]
        assert len(bp) == 3
        assert bp[0]["_layer"] == "stable"
        assert bp[1]["_layer"] == "conversation"
        assert bp[2].get("_layer") is None  # 链尾（exec_context 前）
        # 原消息不被改写
        assert count_breakpoints(round2) == 0
