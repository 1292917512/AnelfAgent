"""任务产出落库治理与反思焦点注入（agent.task.executor）单元测试。"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

from agent.memory.memory_types import MemoryType
from agent.task.executor import TaskExecutor
from agent.task.model import TaskDefinition, TaskResult


def _mind_with_store(store: SimpleNamespace, reflect_return: str = "产出") -> SimpleNamespace:
    return SimpleNamespace(
        pfc=SimpleNamespace(get_tool_use_total=lambda: 0),
        get_recollection=AsyncMock(return_value=[]),
        reflect=AsyncMock(return_value=reflect_return),
        memory_store=store,
        embedder=None,
    )


def _store(**overrides) -> SimpleNamespace:
    base = dict(
        has_similar_content=AsyncMock(return_value=False),
        add=AsyncMock(return_value=1),
        merge_memories=AsyncMock(return_value=9),
        get=AsyncMock(return_value=None),
        update=AsyncMock(return_value=True),
        get_tool_error_stats=AsyncMock(return_value=[]),
        list_recent=AsyncMock(return_value=[]),
    )
    base.update(overrides)
    return SimpleNamespace(**base)


class TestStoreResultDedup:
    async def test_rule_dedup_skips_write(self, monkeypatch) -> None:
        store = _store(has_similar_content=AsyncMock(return_value=True))
        mind = _mind_with_store(store)
        result = TaskResult(task_name="t", content="重复内容", memory_type=MemoryType.EPISODIC, source="t")

        await TaskExecutor(mind)._store_result(result)

        store.add.assert_not_awaited()

    async def test_llm_skip_verdict_skips_write(self, monkeypatch) -> None:
        import agent.memory.dedup as dedup

        monkeypatch.setattr(dedup, "gather_dedup_candidates", AsyncMock(return_value=[SimpleNamespace(id=5)]))
        monkeypatch.setattr(dedup, "judge_write", AsyncMock(return_value={"action": "skip"}))
        monkeypatch.setattr(dedup, "apply_evidence_signals", AsyncMock())
        store = _store()
        mind = _mind_with_store(store)
        result = TaskResult(task_name="t", content="已有等价记忆", memory_type=MemoryType.EPISODIC, source="t")

        await TaskExecutor(mind)._store_result(result)

        store.add.assert_not_awaited()
        dedup.apply_evidence_signals.assert_awaited_once()

    async def test_store_verdict_writes_entry(self, monkeypatch) -> None:
        import agent.memory.dedup as dedup

        monkeypatch.setattr(dedup, "gather_dedup_candidates", AsyncMock(return_value=[]))
        monkeypatch.setattr(dedup, "judge_write", AsyncMock(return_value={"action": "store"}))
        store = _store()
        mind = _mind_with_store(store)
        result = TaskResult(task_name="t", content="全新结论", memory_type=MemoryType.EPISODIC, source="t", importance=0.8)

        await TaskExecutor(mind)._store_result(result)

        store.add.assert_awaited_once()
        entry = store.add.await_args.args[0]
        assert entry.content == "全新结论"
        assert entry.importance == 0.8


class TestReflectionFocus:
    async def test_focus_lines_injected_into_prompt(self, monkeypatch) -> None:
        import agent.planning.situation as situation

        store = _store(
            get_tool_error_stats=AsyncMock(return_value=[
                {"tool_name": "web_fetch", "total": 7, "unresolved": 5},
            ]),
            list_recent=AsyncMock(return_value=[
                SimpleNamespace(id=42, content="上次反思：回复偏冗长"),
            ]),
        )
        monkeypatch.setattr(
            situation, "stale_goal_line",
            AsyncMock(return_value="[目标停滞] 1 个活跃目标 ≥7 天未更新: g1(9天)"),
        )
        mind = _mind_with_store(store)
        executor = TaskExecutor(mind)
        task = TaskDefinition(
            name="self_reflection", prompt="p",
            save_result_to_memory=False, tags=["type:reflection"],
        )

        await executor.run(task, trigger="idle")

        prompt_msg = mind.reflect.await_args.args[0][-1]["content"]
        assert "[反思焦点]" in prompt_msg
        assert "web_fetch×7" in prompt_msg
        assert "目标停滞" in prompt_msg
        assert "上次反思 #42" in prompt_msg

    async def test_no_focus_when_all_sources_empty(self, monkeypatch) -> None:
        import agent.planning.situation as situation

        monkeypatch.setattr(situation, "stale_goal_line", AsyncMock(return_value=""))
        store = _store()
        mind = _mind_with_store(store)
        executor = TaskExecutor(mind)
        task = TaskDefinition(
            name="self_reflection", prompt="p",
            save_result_to_memory=False, tags=["type:reflection"],
        )

        await executor.run(task, trigger="idle")

        prompt_msg = mind.reflect.await_args.args[0][-1]["content"]
        assert "[反思焦点]" not in prompt_msg

    async def test_focus_failure_does_not_break_run(self, monkeypatch) -> None:
        import agent.planning.situation as situation

        monkeypatch.setattr(
            situation, "stale_goal_line",
            AsyncMock(side_effect=RuntimeError("boom")),
        )
        store = _store(get_tool_error_stats=AsyncMock(side_effect=RuntimeError("boom")))
        mind = _mind_with_store(store)
        executor = TaskExecutor(mind)
        task = TaskDefinition(
            name="self_reflection", prompt="p",
            save_result_to_memory=False, tags=["type:reflection"],
        )

        result = await executor.run(task, trigger="idle")
        assert result is not None

    async def test_non_reflection_task_skips_focus(self) -> None:
        store = _store()
        mind = _mind_with_store(store)
        executor = TaskExecutor(mind)
        task = TaskDefinition(name="plain_task", prompt="p", save_result_to_memory=False)

        await executor.run(task, trigger="manual")

        store.get_tool_error_stats.assert_not_awaited()
