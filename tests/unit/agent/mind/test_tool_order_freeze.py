"""工具数组跨回复追加式冻结（ToolAssembly）与 stable 分块缓存单元测试。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

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


class TestReflectToolSchemas:
    """反思循环精简工具目录（ToolAssembly.get_reflect_tool_schemas）：

    常态集 = always + 选择器（默认 heartbeat），回复级热召回/冻结结转不进入；
    更多分组由模型经发现/激活自服务扩展。
    """

    @pytest.fixture(autouse=True)
    def _tools(self):
        from core.entity import EntityRegistry

        EntityRegistry.register_tool(name="ra_always", func=lambda: "ok", group="g_always", tags=["always"])
        EntityRegistry.register_tool(name="ra_heartbeat", func=lambda: "ok", group="g_hb", tags=["heartbeat"])
        EntityRegistry.register_tool(name="ra_core", func=lambda: "ok", group="g_core", tags=["core"])
        EntityRegistry.register_tool(
            name="ra_sleep", func=lambda: "ok", group="g_sleep",
            tags=["always"], allow_sleep=True, sleep_brief="b",
        )
        yield
        for n in ("ra_always", "ra_heartbeat", "ra_core", "ra_sleep"):
            EntityRegistry.unregister(n)

    async def test_lean_default_catalog(self) -> None:
        ta = ToolAssembly()
        names = _names(await ta.get_reflect_tool_schemas(scope="t_reflect_lean"))
        assert "ra_always" in names
        assert "ra_heartbeat" in names  # 默认选择器 heartbeat
        assert "ra_core" not in names   # 非常态标签不进精简目录
        assert "ra_sleep" not in names  # 沉睡分组未激活（门控与回复路径同纪律）

    async def test_selector_matches_tag_and_group(self) -> None:
        ta = ToolAssembly()
        by_tag = _names(await ta.get_reflect_tool_schemas(scope="t_reflect_sel", selectors=["core"]))
        assert "ra_core" in by_tag
        by_group = _names(await ta.get_reflect_tool_schemas(scope="t_reflect_sel", selectors=["g_core"]))
        assert "ra_core" in by_group

    async def test_self_service_activation(self) -> None:
        """模型经 activate_tool_group 唤醒沉睡分组后，重建目录包含其工具。"""
        from agent.mind.tool_activation import tool_activation

        scope = "t_reflect_act"
        tool_activation.activate("g_sleep", rounds=3, scope=scope)
        try:
            ta = ToolAssembly()
            names = _names(await ta.get_reflect_tool_schemas(scope=scope))
            assert "ra_sleep" in names
        finally:
            tool_activation.clear_scope(scope)

    async def test_discovered_tools_included(self) -> None:
        """list_entity_methods 动态发现的工具在重建后保留。"""
        ta = ToolAssembly()
        ta._discovered_tools.add("ra_core")
        names = _names(await ta.get_reflect_tool_schemas(scope="t_reflect_disc"))
        assert "ra_core" in names

    async def test_order_deterministic_across_calls(self) -> None:
        """同一装配输入两次调用产出字节序一致（反思目录跨调用稳定）。"""
        ta = ToolAssembly()
        first = _names(await ta.get_reflect_tool_schemas(scope="t_reflect_ord"))
        second = _names(await ta.get_reflect_tool_schemas(scope="t_reflect_ord"))
        assert first == second
