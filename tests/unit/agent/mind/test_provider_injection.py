"""上下文提供者实时注入（think_loop 尾部）单元测试。

布局约定：provider 快照每轮收集并置于工具链之后、exec_context 之前——
实时内容（时间/天气等）逐轮新鲜，其字节变化不打断工具链前缀缓存。
"""

from __future__ import annotations

import pytest
from helpers.think_loop_fakes import (
    FakeMind,
    FakePfc,
    end_reply_result,
    run_think_loop,
    tool_result,
)

from agent.mind.tools.think_loop import ThinkMode
from core.context_provider import (
    ContextProviderRegistry,
    ProviderMeta,
    ProviderSnapshot,
)


@pytest.fixture(autouse=True)
def clean_registry():
    ContextProviderRegistry.reset()
    yield
    ContextProviderRegistry.reset()


def _register_counter_provider(counter: list) -> None:
    async def _provide(scope: str) -> ProviderSnapshot:
        counter[0] += 1
        return ProviderSnapshot(content=f"[实时] 第{counter[0]}次收集")

    ContextProviderRegistry.register(
        ProviderMeta(name="fresh_demo", provide_fn=_provide),
    )


def _layer_of(msg: dict) -> str:
    return str(msg.get("_layer", ""))


class TestProviderTailInjection:
    async def test_after_tool_chain_before_exec_context(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """两轮回复：provider 消息位于工具链之后、exec_context 之前，且逐轮新鲜。"""
        # 新鲜度阈值归零：每轮强制重收（验证逐轮新鲜语义）
        monkeypatch.setattr(
            "core.context_provider._COLLECT_FRESH_SECONDS", 0.0,
        )
        counter = [0]
        _register_counter_provider(counter)

        mind = FakeMind(
            rounds=[tool_result("", ["recall"]), end_reply_result()],
            default_text=None,
            pfc=FakePfc(exec_layer=True),
        )
        base = [{"role": "system", "content": "BASE", "_layer": "stable"}]
        await run_think_loop(
            mind, mode=ThinkMode.REPLY, base_messages=base, tools=[{"name": "recall"}],
        )

        assert len(mind.sent_messages) == 2
        round2 = mind.sent_messages[1]

        provider_idx = next(
            i for i, m in enumerate(round2) if _layer_of(m) == "provider"
        )
        exec_idx = next(
            i for i, m in enumerate(round2) if _layer_of(m) == "exec_context"
        )
        # 工具链消息无 _layer 标签（recall 的 assistant/tool 回合）
        chain_idx = max(
            i for i, m in enumerate(round2)
            if not m.get("_layer") and m.get("role") != "system"
            or m.get("role") == "tool"
        )
        assert chain_idx < provider_idx < exec_idx

        # 逐轮新鲜：两轮收集内容不同（计数递增）
        r1_provider = next(
            m for m in mind.sent_messages[0] if _layer_of(m) == "provider"
        )
        r2_provider = round2[provider_idx]
        assert r1_provider["content"] == "[实时] 第1次收集"
        assert r2_provider["content"] == "[实时] 第2次收集"

    async def test_no_providers_no_injection(self) -> None:
        """无注册 provider 时消息结构不受影响（零开销零残留）。"""
        mind = FakeMind(
            rounds=[end_reply_result()], default_text=None,
            pfc=FakePfc(exec_layer=True),
        )
        await run_think_loop(mind, mode=ThinkMode.REPLY, base_messages=[])
        round1 = mind.sent_messages[0]
        assert all(_layer_of(m) != "provider" for m in round1)
