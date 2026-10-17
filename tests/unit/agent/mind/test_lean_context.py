"""任务精简上下文（lean）测试：环境注入块剥离 + 永久记忆保留 + 不跑召回。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace as SN
from unittest.mock import AsyncMock

import pytest

from agent.context.preparation import ContextPreparation
from agent.memory.memory_retriever import MemoryRetriever
from agent.mind.recall import coordinator as recollection
from agent.mind.recall.inputs import collect_recollection_inputs


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
    mind = SN(
        char=SN(get_personality_msg=lambda: [{"content": "人格"}]),
        retriever=retriever,
        memory_store=None,  # 替身无记忆库：主标签记忆注入位为空
        _direct_vision=lambda: False,
        _resolve_entity_scope=lambda anything: "user_lean:test",
        context=SN(
            stable_fingerprint=lambda summary, vision: "fp",
            build_persona_block=lambda parts, guide: "人设块",
            build_tools_block=lambda summary, vision: "工具块",
        ),
    )
    mind.context.preparation = ContextPreparation(mind.context)
    mind.context.preparation._file_cache = _FileCache()
    return mind



class TestLeanLayeredPrompts:
    async def test_lean_context_keeps_only_pins(self) -> None:
        """lean：context 层只含永久记忆块，不读便签/文件索引/状态区块。"""
        mind = _fake_mind("[系统注入·永久记忆] 语音教导")
        (_, _, context_text, _, _, _, status_text, heartbeat_text) = (
            await mind.context.preparation.build_layers(
                ["人格"], "user_lean:test", "models", "[系统注入·永久记忆] 语音教导", lean=True,
            )
        )
        assert "语音教导" in context_text
        assert status_text == ""
        assert heartbeat_text == ""
        # 便签/索引/状态均未加载（静态指南属 stable 人设块，正常加载）
        assert "build_dynamic_notes" not in mind.context.preparation._file_cache.calls
        assert "build_file_index_block" not in mind.context.preparation._file_cache.calls
        assert "build_memory_status_block" not in mind.context.preparation._file_cache.calls
        assert "build_heartbeat_status_block" not in mind.context.preparation._file_cache.calls

    async def test_lean_without_pins_has_no_context_block(self) -> None:
        """lean 且无永久记忆：不注入空便签提示，context 层整体缺省。"""
        mind = _fake_mind()
        (_, _, context_text, _, _, _, status_text, heartbeat_text) = (
            await mind.context.preparation.build_layers(["人格"], "user_lean:test", "models", "", lean=True)
        )
        assert context_text == ""
        assert status_text == ""
        assert heartbeat_text == ""

    async def test_full_mode_loads_notes_and_status(self) -> None:
        """非 lean：便签/索引/状态正常加载（回归保护）。"""
        mind = _fake_mind()
        (_, _, context_text, _, _, _, status_text, heartbeat_text) = (
            await mind.context.preparation.build_layers(["人格"], "user_lean:test", "models", "", lean=False)
        )
        assert "build_dynamic_notes" in mind.context.preparation._file_cache.calls
        assert "build_file_index_block" in mind.context.preparation._file_cache.calls
        assert status_text == "罐头"
        assert heartbeat_text == "罐头"
        assert context_text  # 空便签提示或内容拼接，非空


class TestLeanRecollection:
    @pytest.mark.parametrize("query", ["我钥匙放哪了", "WiFi密码", "会议地址", "帮我查下", "/review", "好的？"])
    def test_short_queries_are_not_treated_as_greetings(self, query: str) -> None:
        assert not MemoryRetriever._is_trivial_turn([{"role": "user", "content": query}])

    async def test_greeting_skips_network_recall_but_keeps_profiles_and_gestures(self) -> None:
        mind = _fake_mind()
        mind.embedder = SN(embed_query=AsyncMock())
        mind.retriever.recall_split = AsyncMock(return_value=([{"content": "画像"}], []))
        mind.retriever.load_relation_snippets = AsyncMock(return_value=[])
        mind._extract_related_scopes = lambda *args: []
        mind._recall_cross_channel = AsyncMock()
        mind._match_skills = AsyncMock(return_value=[{"content": "手势技能"}])
        mind._get_models_summary = lambda: "models"
        mind.context.preparation.build_layers = AsyncMock(return_value=("p", "t", "c", True, True, True, "", ""))
        mind.context.build_llm_context = AsyncMock(return_value=[])
        await recollection.get_recollection(mind, [
            {"role": "user", "content": "请记住详细的项目信息"},
            {"role": "user", "content": "[channel:qq]谢谢！"},
        ])
        mind.embedder.embed_query.assert_not_called()
        mind._recall_cross_channel.assert_not_called()
        mind.retriever.recall_split.assert_awaited_once()
        assert mind._match_skills.call_args.args == ("",)
        assert mind.context.build_llm_context.call_args.kwargs["memory_msgs"] == [{"content": "手势技能"}]

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
        mind.context.preparation.build_layers = AsyncMock(
            return_value=("p", "t", "c", True, True, True, "", "")
        )
        mind.context.build_llm_context = AsyncMock(
            return_value=[{"role": "system", "content": "x"}]
        )

        msgs = await recollection.get_recollection(mind, [], None, lean=True)

        assert msgs == [{"role": "system", "content": "x"}]
        kwargs = mind.context.build_llm_context.call_args.kwargs
        assert kwargs["memory_msgs"] == []
        assert kwargs["profile_msgs"] == []
        assert kwargs["relation_msgs"] == []
        assert kwargs["status_text"] == ""
        assert kwargs["heartbeat_text"] == ""
        assert kwargs["hub_text"] == ""  # 无记忆库时主标签记忆位为空
        assert mind.context.preparation.build_layers.call_args.kwargs["lean"] is True


@pytest.mark.parametrize("group", [False, True])
async def test_context_reads_overlap_and_skills_only_wait_for_plan(monkeypatch, group) -> None:
    mind = _fake_mind()
    started = {key: asyncio.Event() for key in ("embed", "memory", "relations", "hub", "summary", "skills", "cross")}
    release_vector, release_cross, release_memory = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def embed(query):
        started["embed"].set()
        await release_vector.wait()
        return [1.0]

    async def recall(tail, *, query_vec_task, plan_ready, **kwargs):
        assert tail == [{"role": "user", "content": query}]
        started["memory"].set()
        assert await asyncio.shield(query_vec_task) == [1.0]
        plan_ready.set_result(["复用计划"])
        await release_memory.wait()
        return [{"content": "画像"}], [{"content": "记忆"}]

    async def skills(query, *, query_vec_task, planned_queries, **kwargs):
        assert "internal" not in query
        assert planned_queries == ["复用计划"]
        assert await query_vec_task == [1.0]
        started["skills"].set()
        return [{"content": "技能"}]

    async def cross(*args, query_vec_task, user_scope):
        assert args[0] == [{"role": "user", "content": query}]
        assert user_scope == "user_qq:1"
        assert await asyncio.shield(query_vec_task) == [1.0]
        started["cross"].set()
        await release_cross.wait()
        return [{"content": "跨频道"}]

    async def read(key, value):
        started[key].set()
        return value

    mind.embedder = SN(embed_query=AsyncMock(side_effect=embed))
    mind.retriever.recall_split = recall
    mind.retriever.load_relation_snippets = lambda scopes: read("relations", [{"content": "关系"}])
    mind._extract_related_scopes = lambda *args: []
    mind._extract_scopes_from_anything = lambda *args: []
    mind._match_skills = skills
    mind._recall_cross_channel = cross
    mind.memory_store = object()
    mind.conversation_data = SN(get_conversation_summary=lambda anything: read("summary", {"summary": "摘要"}))
    monkeypatch.setattr("agent.memory.hub.load_hub_block", lambda store: read("hub", "主标签"))
    monkeypatch.setattr("agent.memory.self_profile.load_self_profile", AsyncMock(return_value=None))
    monkeypatch.setattr("agent.memory.self_profile.render_self_profile_block", lambda _: "自身")
    monkeypatch.setattr("agent.memory.reflection_lifecycle.load_confirmed_block", AsyncMock(return_value="确认"))
    monkeypatch.setattr("agent.memory.reflection_lifecycle.load_verification_block", AsyncMock(return_value="求证"))
    query = ("[channel:qq][group_id:100][uid:1]" if group else "") + "帮我查询之前记录的详细项目设计方案"
    task = asyncio.create_task(collect_recollection_inputs(
        mind, [{"role": "user", "content": query},
               *({"role": "tool", "content": "internal result"} for _ in range(12)),
               {"role": "assistant", "content": "internal thought", "_layer": "provider"}],
        SN(uid=0 if group else "1"),
        scope="group_qq:100" if group else "user_qq:1", adapter="qq", lean=False,
    ))
    try:
        async with asyncio.timeout(2):
            await asyncio.gather(*(started[key].wait() for key in ("embed", "memory", "relations", "hub", "summary")))
            assert not task.done()
            release_vector.set()
            await asyncio.gather(started["skills"].wait(), started["cross"].wait())
            assert not task.done()
            release_cross.set()
            assert not task.done()
            release_memory.set()
            result = await task
        assert result.profile_blocks == ["自身", "确认", "求证"]
        assert result.memories == [{"content": "记忆"}]
        assert result.skills == [{"content": "技能"}]
        assert result.cross_channel == [{"content": "跨频道"}]
        assert result.hub == "主标签" and result.summary == {"summary": "摘要"}
        mind.embedder.embed_query.assert_awaited_once()
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize("fail", [False, True])
async def test_context_exit_drains_sibling_reads(monkeypatch, fail) -> None:
    mind = _fake_mind()
    started, closed = set(), set()
    ready, trigger = asyncio.Event(), asyncio.Event()

    async def wait(name):
        started.add(name)
        if len(started) == 4:
            ready.set()
        try:
            await asyncio.Event().wait()
        finally:
            closed.add(name)

    async def hub(store):
        await trigger.wait()
        raise RuntimeError("hub unavailable")

    async def recall(*args, plan_ready, **kwargs):
        plan_ready.set_result(["共享查询"])
        return await wait("memory")

    mind.embedder = SN(embed_query=lambda query: wait("embed"))
    mind.retriever.recall_split = recall
    mind.retriever.load_relation_snippets = lambda *args: wait("relations")
    mind._extract_related_scopes = lambda *args: []
    mind._recall_cross_channel = AsyncMock(return_value=[])
    mind._match_skills = lambda *args, **kwargs: wait("skills")
    mind.memory_store = object()
    monkeypatch.setattr("agent.memory.hub.load_hub_block", hub)
    monkeypatch.setattr("agent.memory.self_profile.load_self_profile", AsyncMock(return_value=None))
    monkeypatch.setattr("agent.memory.self_profile.render_self_profile_block", lambda _: "")
    monkeypatch.setattr("agent.memory.reflection_lifecycle.load_confirmed_block", AsyncMock(return_value=""))
    monkeypatch.setattr("agent.memory.reflection_lifecycle.load_verification_block", AsyncMock(return_value=""))
    task = asyncio.create_task(collect_recollection_inputs(
        mind, [{"role": "user", "content": "请查找之前讨论的完整方案"}], None,
        scope="user_qq:1", adapter="qq", lean=False,
    ))
    try:
        await asyncio.wait_for(ready.wait(), 2)
        if fail:
            trigger.set()
            with pytest.raises(ExceptionGroup, match="TaskGroup"):
                await task
        else:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        assert closed == started == {"embed", "memory", "relations", "skills"}
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
