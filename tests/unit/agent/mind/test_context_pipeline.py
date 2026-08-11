"""上下文构建管线（context_pipeline）单元测试：声明注册 / 变动率排序 / 布局覆盖 / 断点注入。"""

from __future__ import annotations

from agent.mind.context_pipeline import (
    VOL_HISTORY,
    VOL_LOW,
    VOL_SESSION,
    VOL_STABLE,
    ContextInput,
    ContextPipeline,
    context_block,
)


class _Host:
    """声明式块宿主替身。"""

    @context_block("memory", VOL_SESSION)
    def _blk_recall(self, inp: ContextInput):
        return [{"role": "system", "content": "召回"}]

    @context_block("stable", VOL_STABLE)
    def _blk_persona(self, inp: ContextInput):
        return [{"role": "system", "content": "人设"}]

    @context_block("conversation", VOL_HISTORY)
    def _blk_history(self, inp: ContextInput):
        return [{"role": "user", "content": "历史"}]

    @context_block("context", VOL_LOW)
    def _blk_notes(self, inp: ContextInput):
        return [{"role": "system", "content": "便签"}]


class _AsyncHost:
    @context_block("provider", VOL_SESSION + 4)
    async def _blk_provider(self, inp: ContextInput):
        return [{"role": "system", "content": "提供者"}]

    @context_block("stable", VOL_STABLE)
    def _blk_persona(self, inp: ContextInput):
        return [{"role": "system", "content": "人设"}]


class TestPipelineOrdering:
    async def test_sorted_by_volatility_not_declaration(self) -> None:
        """块顺序由变动率决定，与方法定义顺序无关。"""
        pipeline = ContextPipeline(_Host())
        msgs = await pipeline.build(ContextInput())
        assert [m["_layer"] for m in msgs] == ["stable", "context", "conversation", "memory"]

    async def test_empty_blocks_skipped(self) -> None:
        class EmptyHost:
            @context_block("stable", VOL_STABLE)
            def _blk_a(self, inp):
                return [{"role": "system", "content": ""}]  # 空内容跳过

            @context_block("context", VOL_LOW)
            def _blk_b(self, inp):
                return []

        msgs = await ContextPipeline(EmptyHost()).build(ContextInput())
        assert msgs == []

    async def test_async_builder_supported(self) -> None:
        msgs = await ContextPipeline(_AsyncHost()).build(ContextInput())
        assert [m["_layer"] for m in msgs] == ["stable", "provider"]

    async def test_override_reorders_layers(self) -> None:
        """变动率覆盖表（legacy 布局）：动态块移到历史之前。"""
        pipeline = ContextPipeline(
            _Host(),
            volatility_overrides={"memory": 25, "conversation": 32},
        )
        msgs = await pipeline.build(ContextInput())
        assert [m["_layer"] for m in msgs] == ["stable", "context", "memory", "conversation"]


class TestBreakpointInjection:
    async def test_pipeline_never_injects_breakpoints(self) -> None:
        """管线只负责 _layer 标签：断点装饰统一在发送边界（llm/prompt_cache）。"""
        pipeline = ContextPipeline(_Host())
        msgs = await pipeline.build(ContextInput())
        assert not [m for m in msgs if m.get("cache_control")]
        # 锚点选择所需的层标签完整
        layers = [m["_layer"] for m in msgs]
        assert "stable" in layers and "context" in layers and "conversation" in layers


class TestLayerRegistry:
    def test_decorator_registers_meta(self) -> None:
        """装饰器自动注册层元数据（label 缺省用层名）。"""
        from agent.mind.context_pipeline import context_block, get_layer_meta

        class _LocalHost:
            @context_block("ut_custom_layer", VOL_SESSION, "自定义展示名")
            def _blk(self, inp):
                return []

        meta = get_layer_meta("ut_custom_layer")
        assert meta is not None
        assert meta.volatility == VOL_SESSION
        assert meta.label == "自定义展示名"

    def test_builtin_layers_registered(self) -> None:
        """think_loop 管理的层（tool_chain/exec_context）同样在册。"""
        from agent.mind.context_pipeline import get_layer_meta

        chain = get_layer_meta("tool_chain")
        assert chain is not None and chain.managed == "think_loop"
        exec_ctx = get_layer_meta("exec_context")
        assert exec_ctx is not None and exec_ctx.volatility_label == "每轮"

    def test_order_derived_from_registry(self) -> None:
        """层序由注册表按变动率推导（含管线层与 think_loop 层）。"""
        from agent.mind.context_pipeline import get_layer_order, register_layer

        register_layer("zzz_custom", 45, "自定义层")
        order = get_layer_order()
        assert order.index("conversation") < order.index("zzz_custom") < order.index("tool_chain")
        assert order[-1] == "exec_context"

    def test_real_assembly_layers_have_labels(self) -> None:
        """真实 ContextAssembly 的 11 个层全部带中文展示名注册。"""
        # 导入即触发块装饰注册
        import agent.mind.context_assembly  # noqa: F401
        from agent.mind.context_pipeline import get_layer_meta

        for layer, expect in (
            ("stable", "人设"), ("context", "便签"), ("summary", "摘要"),
            ("conversation", "对话历史"), ("profile", "画像"), ("memory", "召回"),
        ):
            meta = get_layer_meta(layer)
            assert meta is not None and expect in meta.label, layer


class TestGoldenOrder:
    async def test_full_stack_default_order(self) -> None:
        """全块注入时的完整顺序（默认布局，动静分离）。"""
        from types import SimpleNamespace as SN
        from unittest.mock import AsyncMock

        from agent.mind.prefrontal_cortex import PrefrontalCortex

        conversation_data = SN(
            max_size=30,
            get_conversation_record_by_everything=AsyncMock(return_value=[]),
            count_messages=AsyncMock(return_value=0),
        )
        pfc = PrefrontalCortex(everything_data=SN(), conversation_data=conversation_data)
        msgs = await pfc.build_llm_context(
            persona_text="人设", tools_text="工具", context_text="便签",
            status_text="状态",
            memory_msgs=[{"role": "system", "content": "召回"}],
            profile_msgs=[{"role": "system", "content": "画像"}],
            summary_row={"summary": "摘要", "watermarks": {}, "folded_count": 20},
            prefetched_conversation=[
                {"role": "assistant", "content": "答"},
                {"role": "user", "content": "问"},
            ],
            anything=SN(uid=1, group_id=0),
        )
        layers = [m["_layer"] for m in msgs]
        assert layers == [
            "stable", "stable", "summary",
            "conversation", "conversation",
            "context", "status", "profile", "memory",
        ], layers
        # 会话消息角色保持 DB 原样（尾部为 user，不触发 prefill 修复）
        conv = [m for m in msgs if m["_layer"] == "conversation"]
        assert [m["role"] for m in conv] == ["assistant", "user"]

    async def test_legacy_order_via_overrides(self, monkeypatch) -> None:
        """legacy 布局：动态块全部移到历史之前（覆盖表驱动）。"""
        from types import SimpleNamespace as SN
        from unittest.mock import AsyncMock

        import agent.mind.context_assembly as ca
        from agent.mind.prefrontal_cortex import PrefrontalCortex

        monkeypatch.setattr(ca, "_tail_injection_enabled", lambda: False)
        conversation_data = SN(
            max_size=30,
            get_conversation_record_by_everything=AsyncMock(return_value=[]),
            count_messages=AsyncMock(return_value=0),
        )
        pfc = PrefrontalCortex(everything_data=SN(), conversation_data=conversation_data)
        msgs = await pfc.build_llm_context(
            persona_text="人设", tools_text="工具", context_text="便签",
            status_text="状态",
            memory_msgs=[{"role": "system", "content": "召回"}],
            profile_msgs=[{"role": "system", "content": "画像"}],
            summary_row={"summary": "摘要", "watermarks": {}, "folded_count": 20},
            prefetched_conversation=[{"role": "user", "content": "问"}],
            anything=SN(uid=1, group_id=0),
        )
        layers = [m["_layer"] for m in msgs]
        # 历史与摘要在最后；动态块在前
        assert layers[-2:] == ["summary", "conversation"]
        assert layers.index("profile") < layers.index("conversation")
        assert layers.index("status") < layers.index("conversation")
