"""任务精简上下文（lean）测试：环境注入块剥离 + 永久记忆保留 + 不跑召回。"""

from __future__ import annotations

from types import SimpleNamespace as SN
from unittest.mock import AsyncMock

from agent.mind import recollection


class _FileCache:
    """记录 get_or_load 调用（按构建函数名），返回罐头值。"""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def get_or_load(self, path: str, build):  # noqa: ANN001, ANN202
        self.calls.append(getattr(build, "__name__", str(build)))
        return ("罐头", False)


def _fake_mind(pins_text: str = "") -> SN:
    retriever = SN(
        _load_permanent_pins=AsyncMock(return_value=[{"id": 1}] if pins_text else []),
        _format_unified_results=AsyncMock(
            return_value=[{"role": "system", "content": pins_text}] if pins_text else []
        ),
    )
    return SN(
        char=SN(get_personality_msg=lambda: [{"content": "人格"}]),
        retriever=retriever,
        _direct_vision=lambda: False,
        _file_cache=_FileCache(),
        _resolve_entity_scope=lambda anything: "user_lean:test",
        pfc=SN(
            stable_fingerprint=lambda summary, vision: "fp",
            context_assembly=SN(
                build_persona_block=lambda parts, guide: "人设块",
                build_tools_block=lambda summary, vision: "工具块",
            ),
        ),
    )


class TestLeanLayeredPrompts:
    async def test_lean_context_keeps_only_pins(self) -> None:
        """lean：context 层只含永久记忆块，不读便签/文件索引/状态区块。"""
        mind = _fake_mind("[系统注入·永久记忆] 语音教导")
        (_, _, context_text, _, _, _, status_text) = (
            await recollection._build_layered_prompts(
                mind, None, "models", "[系统注入·永久记忆] 语音教导", lean=True,
            )
        )
        assert "语音教导" in context_text
        assert status_text == ""
        # 便签/索引/状态均未加载（静态指南属 stable 人设块，正常加载）
        assert "build_dynamic_notes" not in mind._file_cache.calls
        assert "build_file_index_block" not in mind._file_cache.calls
        assert "build_memory_status_block" not in mind._file_cache.calls

    async def test_lean_without_pins_has_no_context_block(self) -> None:
        """lean 且无永久记忆：不注入空便签提示，context 层整体缺省。"""
        mind = _fake_mind()
        (_, _, context_text, _, _, _, status_text) = (
            await recollection._build_layered_prompts(mind, None, "models", "", lean=True)
        )
        assert context_text == ""
        assert status_text == ""

    async def test_full_mode_loads_notes_and_status(self) -> None:
        """非 lean：便签/索引/状态正常加载（回归保护）。"""
        mind = _fake_mind()
        (_, _, context_text, _, _, _, status_text) = (
            await recollection._build_layered_prompts(mind, None, "models", "", lean=False)
        )
        assert "build_dynamic_notes" in mind._file_cache.calls
        assert "build_file_index_block" in mind._file_cache.calls
        assert status_text == "罐头"
        assert context_text  # 空便签提示或内容拼接，非空


class TestLeanRecollection:
    async def test_lean_skips_all_recall_paths(self) -> None:
        """lean 的 get_recollection：不 embed、不召回，环境注入位全空。"""
        mind = _fake_mind("永久")
        mind.embedder = SN(
            embed_query=AsyncMock(side_effect=AssertionError("lean 不应 embed"))
        )
        mind.retriever.recall_split = AsyncMock(
            side_effect=AssertionError("lean 不应召回")
        )
        mind._get_models_summary = lambda: "models"
        mind._extract_related_scopes = lambda conv, scope: []
        mind._build_layered_prompts = AsyncMock(
            return_value=("p", "t", "c", True, True, True, "")
        )
        mind._resolve_target_id = lambda anything: ""
        mind.pfc.build_llm_context = AsyncMock(
            return_value=[{"role": "system", "content": "x"}]
        )

        msgs = await recollection.get_recollection(mind, [], None, lean=True)

        assert msgs == [{"role": "system", "content": "x"}]
        kwargs = mind.pfc.build_llm_context.call_args.kwargs
        assert kwargs["memory_msgs"] == []
        assert kwargs["profile_msgs"] == []
        assert kwargs["relation_msgs"] == []
        assert kwargs["goal_msgs"] == []
        assert kwargs["status_text"] == ""
        assert mind._build_layered_prompts.call_args.kwargs["lean"] is True
