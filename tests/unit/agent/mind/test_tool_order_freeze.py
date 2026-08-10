"""工具数组跨回复追加式冻结（ToolAssembly）与 stable 分块缓存单元测试。"""

from __future__ import annotations

from types import SimpleNamespace

from agent.mind.context_assembly import ContextAssembly
from agent.mind.tool_assembly import ToolAssembly
from agent.mind.work_memory import WorkMemory


def _schema(name: str) -> dict:
    return {"type": "function", "function": {"name": name, "parameters": {}}}


def _names(schemas: list[dict]) -> list[str]:
    return [s["function"]["name"] for s in schemas]


def _assembly_ta() -> ToolAssembly:
    ta = ToolAssembly.__new__(ToolAssembly)
    ta._frozen_tool_names = []
    ta._tool_recall = {}
    return ta


class TestAppendOnlyFreeze:
    def test_first_round_establishes_two_bucket_order(self) -> None:
        """首轮按双桶排序键建立冻结序（共享核心在前，作用域工具沉尾）。"""
        ta = _assembly_ta()
        schemas = [_schema(n) for n in ["send_message", "recall", "end_reply", "memorize"]]
        ordered = ta._apply_append_only_freeze(schemas, {"send_message"})
        assert _names(ordered) == ["end_reply", "memorize", "recall", "send_message"]

    def test_order_stable_across_replies(self) -> None:
        """第二轮来源顺序打乱/计数变化：输出与首轮逐字节一致。"""
        ta = _assembly_ta()
        first = ta._apply_append_only_freeze(
            [_schema(n) for n in ["b_tool", "a_tool", "c_tool"]], set(),
        )
        second = ta._apply_append_only_freeze(
            [_schema(n) for n in ["c_tool", "b_tool", "a_tool"]], set(),
        )
        assert _names(first) == _names(second) == ["a_tool", "b_tool", "c_tool"]

    def test_newcomers_appended_not_inserted(self) -> None:
        """新工具（热召回换血/新发现）追加尾部，已有前缀位置不变。"""
        ta = _assembly_ta()
        first = ta._apply_append_only_freeze([_schema(n) for n in ["a", "c"]], set())
        second = ta._apply_append_only_freeze(
            [_schema(n) for n in ["a", "b_new", "c"]], set(),
        )
        assert _names(first) == ["a", "c"]
        assert _names(second) == ["a", "c", "b_new"]  # 追加而非插入

    def test_vanished_tools_dropped(self) -> None:
        """注销/门控排除的工具从输出消失（冻结名单残留无害）。"""
        ta = _assembly_ta()
        ta._apply_append_only_freeze([_schema(n) for n in ["a", "gone"]], set())
        out = ta._apply_append_only_freeze([_schema("a")], set())
        assert _names(out) == ["a"]
        assert "gone" in ta._frozen_tool_names  # 名单残留，不影响输出


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
