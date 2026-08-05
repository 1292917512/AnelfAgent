"""上下文动静分离布局单元测试：消息顺序 / 摘要断点 / 尾部注入回退。"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from agent.mind.message_schema import normalize_for_send
from agent.mind.prefrontal_cortex import PrefrontalCortex


def _pfc() -> PrefrontalCortex:
    conversation_data = SimpleNamespace(
        max_size=30,
        get_conversation_record_by_everything=AsyncMock(return_value=[]),
        count_messages=AsyncMock(return_value=0),
    )
    return PrefrontalCortex(
        everything_data=SimpleNamespace(),
        conversation_data=conversation_data,
    )


def _layers(msgs: list[dict]) -> list[str]:
    return [m.get("_layer", "") for m in msgs]


_BASE_KWARGS = dict(
    persona_text="人设",
    tools_text="工具",
    context_text="便签",
    memory_msgs=[{"role": "system", "content": "召回"}],
    profile_msgs=[{"role": "system", "content": "画像"}],
    summary_row={"summary": "早期摘要", "watermarks": {}, "folded_count": 20},
    prefetched_conversation=[{"role": "user", "content": "你好"}],
    anything=SimpleNamespace(uid=1, group_id=0),
)


class TestTailInjectionLayout:
    async def test_dynamic_zone_after_history(self) -> None:
        """默认布局：stable → 摘要 → 历史 → 尾部动态区（画像/召回在历史之后）。"""
        pfc = _pfc()
        msgs = await pfc.build_llm_context(**_BASE_KWARGS)
        assert _layers(msgs) == [
            "stable", "stable", "context",
            "summary",
            "conversation",
            "profile", "memory",
        ]

    async def test_summary_breakpoint_injected(self) -> None:
        """Anthropic 模式下第 4 断点打在对话历史末尾（历史纯追加，断点随之前移）。"""
        pfc = _pfc()
        msgs = await pfc.build_llm_context(**_BASE_KWARGS, anthropic_breakpoint=True)
        conversation = [m for m in msgs if m.get("_layer") == "conversation"]
        assert conversation[-1].get("cache_control") == {"type": "ephemeral"}
        summary = next(m for m in msgs if m.get("_layer") == "summary")
        assert "cache_control" not in summary
        breakpoints = [m for m in msgs if m.get("cache_control")]
        # 人设/工具/便签/历史末尾 = 4 个断点（Anthropic 上限）
        assert len(breakpoints) == 4

    async def test_breakpoint_falls_back_to_summary(self) -> None:
        """无对话历史时第 4 断点回退到摘要块。"""
        pfc = _pfc()
        kwargs = {**_BASE_KWARGS, "prefetched_conversation": []}
        msgs = await pfc.build_llm_context(**kwargs, anthropic_breakpoint=True)
        summary = next(m for m in msgs if m.get("_layer") == "summary")
        assert summary.get("cache_control") == {"type": "ephemeral"}

    async def test_no_summary_breakpoint_on_history(self) -> None:
        """无摘要行时无摘要块，第 4 断点落在对话历史末尾。"""
        pfc = _pfc()
        kwargs = {**_BASE_KWARGS, "summary_row": None}
        msgs = await pfc.build_llm_context(**kwargs, anthropic_breakpoint=True)
        assert not [m for m in msgs if m.get("_layer") == "summary"]
        breakpoints = [m for m in msgs if m.get("cache_control")]
        assert len(breakpoints) == 4
        conversation = [m for m in msgs if m.get("_layer") == "conversation"]
        assert conversation[-1].get("cache_control") == {"type": "ephemeral"}

    async def test_legacy_layout_when_disabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """tail_injection 关闭时回退旧布局（动态内容在历史之前）。"""
        monkeypatch.setattr(
            "agent.mind.context_assembly._tail_injection_enabled", lambda: False,
        )
        pfc = _pfc()
        msgs = await pfc.build_llm_context(**_BASE_KWARGS)
        layers = _layers(msgs)
        # 旧布局：画像/召回在历史之前，摘要紧挨历史之前
        assert layers.index("profile") < layers.index("conversation")
        assert layers.index("memory") < layers.index("conversation")
        assert layers.index("summary") < layers.index("conversation")


class TestNormalizePreservesBreakpoint:
    def test_head_system_keeps_role_and_breakpoint(self) -> None:
        """头部连续 system（含摘要块）保持 system 角色且断点透传。"""
        msgs = [
            {"role": "system", "content": "人设", "cache_control": {"type": "ephemeral"}},
            {"role": "system", "content": "摘要", "cache_control": {"type": "ephemeral"}},
            {"role": "user", "content": "历史"},
            {"role": "system", "content": "召回"},
        ]
        out = normalize_for_send(msgs)
        assert out[0]["role"] == "system"
        assert out[0]["cache_control"] == {"type": "ephemeral"}
        assert out[1]["role"] == "system"
        assert out[1]["cache_control"] == {"type": "ephemeral"}
        # 历史之后的动态注入转 user
        assert out[3]["role"] == "user"


class TestCapNames:
    def test_short_list_unchanged(self) -> None:
        from agent.mind.context_assembly import _cap_names
        assert _cap_names(["a", "b"]) == "a, b"

    def test_long_list_capped(self) -> None:
        from agent.mind.context_assembly import _cap_names
        names = [f"tool_{i}" for i in range(47)]
        out = _cap_names(names)
        assert "tool_7" in out and "tool_8" not in out
        assert "等 47 个" in out
