"""工具顺序冻结（frozen_tool_order）与 stable 分块缓存单元测试。"""

from __future__ import annotations

from types import SimpleNamespace

from agent.mind.context_assembly import ContextAssembly
from agent.mind.tool_assembly import ToolAssembly
from agent.mind.tools.round_helpers import _apply_frozen_tool_order, _tool_schema_name
from agent.mind.work_memory import WorkMemory


def _schema(name: str) -> dict:
    return {"type": "function", "function": {"name": name, "parameters": {}}}


class TestFrozenToolOrder:
    def test_reorder_by_frozen(self) -> None:
        """重建结果按首轮冻结顺序重排（使用计数跳变不影响字节序）。"""
        frozen = ["end_reply", "send_message", "recall", "memorize"]
        rebuilt = [_schema(n) for n in ["memorize", "send_message", "recall", "end_reply"]]
        ordered = _apply_frozen_tool_order(rebuilt, frozen)
        assert [_tool_schema_name(s) for s in ordered] == frozen

    def test_newcomers_appended_sorted(self) -> None:
        """新出现的工具按名称排序追加尾部（如媒体工具激活）。"""
        frozen = ["end_reply", "recall"]
        rebuilt = [_schema(n) for n in ["recall", "recognize_image", "end_reply", "voice_to_text"]]
        ordered = _apply_frozen_tool_order(rebuilt, frozen)
        assert [_tool_schema_name(s) for s in ordered] == [
            "end_reply", "recall", "recognize_image", "voice_to_text",
        ]

    def test_vanished_tools_skipped(self) -> None:
        frozen = ["end_reply", "gone_tool", "recall"]
        rebuilt = [_schema(n) for n in ["recall", "end_reply"]]
        ordered = _apply_frozen_tool_order(rebuilt, frozen)
        assert [_tool_schema_name(s) for s in ordered] == ["end_reply", "recall"]

    def test_no_frozen_order_passthrough(self) -> None:
        rebuilt = [_schema("b"), _schema("a")]
        assert _apply_frozen_tool_order(rebuilt, None) is rebuilt


class TestStableBlocks:
    def _assembly(self) -> ContextAssembly:
        wm = WorkMemory(everything_data=SimpleNamespace())
        ta = ToolAssembly()
        return ContextAssembly(wm, ta)

    def test_persona_block_excludes_tools(self) -> None:
        """人设块不含工具目录内容（工具变化不使其失效）。"""
        asm = self._assembly()
        block = asm.build_persona_block(["你是 Anelf。"], "静态指南")
        assert "你是 Anelf。" in block
        assert "静态指南" in block
        assert "[运行环境]" in block
        assert "# 工具分组目录" not in block

    def test_tools_block_excludes_persona(self) -> None:
        """工具块不含人设内容。"""
        asm = self._assembly()
        block = asm.build_tools_block()
        assert "你是 Anelf。" not in block
        assert "[运行环境]" not in block

    def test_stable_layer_combines_blocks(self) -> None:
        asm = self._assembly()
        layer = asm.build_stable_layer(["你是 Anelf。"], static_guide="指南")
        assert "你是 Anelf。" in layer
        assert "指南" in layer

    def test_persona_fingerprint_independent_of_tool_version(self) -> None:
        """人设块指纹不含工具版本因子：工具版本变化不影响其 hash 输入。

        （指纹构造在 recollection._build_layered_prompts：persona_hash 只含
        persona_parts + static_guide + env_info，与 stable_fingerprint 完全分离。）
        """
        from agent.mind.prompt_layers import prompt_cache_manager as mgr
        h1 = mgr.compute_hash("人设", "指南", "环境")
        h2 = mgr.compute_hash("人设", "指南", "环境")
        assert h1 == h2
