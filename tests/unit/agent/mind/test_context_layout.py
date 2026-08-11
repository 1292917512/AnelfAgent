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
        """默认布局：stable → 摘要 → 历史 → 尾部动态区（便签在最前，画像/召回随后）。"""
        pfc = _pfc()
        msgs = await pfc.build_llm_context(**_BASE_KWARGS)
        assert _layers(msgs) == [
            "stable", "stable",
            "summary",
            "conversation",
            "context",
            "profile", "memory",
        ]

    async def test_summary_breakpoint_injected(self) -> None:
        """发送边界装饰：断点打在 stable 层末 + 对话历史末尾（便签在尾部不占断点）。"""
        from agent.llm.prompt_cache import decorate_messages
        pfc = _pfc()
        msgs = await pfc.build_llm_context(**_BASE_KWARGS)
        assert not [m for m in msgs if m.get("cache_control")]  # 管线不注入
        msgs = decorate_messages(msgs, anthropic=True)
        conversation = [m for m in msgs if m.get("_layer") == "conversation"]
        assert conversation[-1].get("cache_control") == {"type": "ephemeral"}
        summary = next(m for m in msgs if m.get("_layer") == "summary")
        assert "cache_control" not in summary
        context = next(m for m in msgs if m.get("_layer") == "context")
        assert "cache_control" not in context
        breakpoints = [m for m in msgs if m.get("cache_control")]
        # stable 层末（工具块）/历史末尾 = 2 个，
        # 余量预留给工具链尾锚点与 tools 数组断点（Anthropic 上限 4）
        assert len(breakpoints) == 2
        assert breakpoints[0]["_layer"] == "stable"
        assert breakpoints[1]["_layer"] == "conversation"

    async def test_breakpoint_falls_back_to_summary(self) -> None:
        """无对话历史时历史锚点回退到摘要块。"""
        from agent.llm.prompt_cache import decorate_messages
        pfc = _pfc()
        kwargs = {**_BASE_KWARGS, "prefetched_conversation": []}
        msgs = await pfc.build_llm_context(**kwargs)
        msgs = decorate_messages(msgs, anthropic=True)
        summary = next(m for m in msgs if m.get("_layer") == "summary")
        assert summary.get("cache_control") == {"type": "ephemeral"}

    async def test_no_summary_breakpoint_on_history(self) -> None:
        """无摘要行时无摘要块，历史锚点落在对话历史最后一条。"""
        from agent.llm.prompt_cache import decorate_messages
        pfc = _pfc()
        kwargs = {**_BASE_KWARGS, "summary_row": None}
        msgs = await pfc.build_llm_context(**kwargs)
        msgs = decorate_messages(msgs, anthropic=True)
        assert not [m for m in msgs if m.get("_layer") == "summary"]
        breakpoints = [m for m in msgs if m.get("cache_control")]
        assert len(breakpoints) == 2
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
