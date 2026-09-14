"""规划态势快照测试：版本化重建 / 渲染口径 / provider 注册 / 错误自纠上下文。

覆盖 agent.planning.situation 的三张消费面：
- 注入（render/_provide）：scope 可见性（present_plan 隔离、reflect 缺席）、
  步骤状态渲染、变更后（invalidate）下一轮即见最新事实
- 错误简报（active_goal_briefs）：goal CRUD 工具 not_found 错误的自纠上下文
- 摘要行（active_goal_lines）：自主循环态势收集口径
"""

from __future__ import annotations

import json

import pytest

from agent.memory.memory_store import MemoryStore
from agent.memory.memory_types import MemoryType
from agent.planning import situation, tracker
from agent.planning import tools as planning_tools
from agent.planning.tracker import planning_store_port


@pytest.fixture
async def store(tmp_path):
    s = MemoryStore(str(tmp_path / "memory.sqlite3"))
    planning_store_port.set(s)
    situation.reset()
    yield s
    await s.close()
    planning_store_port.unbind()
    situation.reset()


async def _render(scope: str = "user_qq:1") -> str:
    await situation.ensure_snapshot()
    return situation.render(scope)


class TestRender:
    async def test_goal_with_steps_visible(self, store):
        """活跃目标渲染：goal_id / 标题 / 进度 / 步骤状态标记。"""
        raw = await planning_tools.create_goal("整理周报", steps="收集|汇总|发送")
        goal = json.loads(raw)["goal"]

        content = await _render()
        assert "[规划态势]" in content
        assert goal["goal_id"] in content
        assert "整理周报" in content
        assert "0/3 步" in content
        assert "○收集" in content  # pending 标记 + 步骤内容

    async def test_step_progress_refreshes_after_update(self, store):
        """步骤推进后（invalidate）渲染立即反映新状态——修复回复中途态势过期。"""
        raw = await planning_tools.create_goal("目标", steps="a|b")
        goal_id = json.loads(raw)["goal"]["goal_id"]

        update_result = await planning_tools.update_goal(
            goal_id, step_index=0, step_status="completed",
        )
        assert json.loads(update_result)["success"] is True
        content = await _render()
        assert "1/2 步" in content
        assert "✓a" in content
        assert "▶b" in content  # 自动推进：下一步 in_progress

    async def test_terminal_goals_vanish(self, store):
        """完成/删除后的目标不再注入（快照与存储严格同步）。"""
        raw = await planning_tools.create_goal("一次性目标")
        goal_id = json.loads(raw)["goal"]["goal_id"]

        await planning_tools.delete_goal(goal_id)
        assert await _render() == ""

    async def test_no_active_goals_renders_empty(self, store):
        """无活跃目标零注入。"""
        assert await _render() == ""

    async def test_reflect_scope_skipped(self, store):
        """reflect（任务/子代理）scope 不注入，保持 lean 精简语义。"""
        await planning_tools.create_goal("任意目标")
        await situation.ensure_snapshot()
        assert situation.render("reflect:abc12345") == ""
        assert await situation._provide("reflect:abc12345") is None

    async def test_present_plan_scope_isolation(self, store):
        """对话内计划（present_plan）仅归属 scope 可见，长期目标全局可见。"""
        await tracker.submit_plan("user_qq:1", "会话内计划", tracker.parse_steps("步骤一"))
        await planning_tools.create_goal("长期目标")

        own = await _render("user_qq:1")
        assert "会话内计划" in own
        assert "长期目标" in own
        assert own.index("会话内计划") < own.index("长期目标")  # 执行计划置顶

        other = await _render("user_qq:2")
        assert "会话内计划" not in other
        assert "长期目标" in other

    async def test_snapshot_version_invalidation(self, store):
        """写路径 invalidate 后快照重建（版本失配触发，无需等再同步窗口）。"""
        raw = await planning_tools.create_goal("旧标题")
        goal_id = json.loads(raw)["goal"]["goal_id"]
        await situation.ensure_snapshot()
        assert "旧标题" in situation.render("user_qq:1")

        # 越过失效钩子的直写（模拟外部编辑）：版本不变时仍读缓存
        entries = await store.list_by_source(
            "goal", memory_type=MemoryType.SEMANTIC, limit=10,
        )
        goal = json.loads(entries[0].content)
        goal["title"] = "新标题"
        entries[0].content = json.dumps(goal, ensure_ascii=False)
        await store.update(entries[0], clear_embedding=True)
        assert goal_id in situation.render("user_qq:1")  # 缓存仍有效（旧标题）
        assert "新标题" not in situation.render("user_qq:1")

        situation.invalidate()
        await situation.ensure_snapshot()
        assert "新标题" in situation.render("user_qq:1")


class TestProviderRegistration:
    async def test_plan_ops_registered(self, store):
        """provider 经模块导入注册（plan_ops，planning 组联动 + 注入开关）。"""
        from core.context_provider import ContextProviderRegistry
        situation._register_provider()  # registry 可能被其他测试 reset，幂等重挂
        meta = ContextProviderRegistry._providers.get("plan_ops")
        assert meta is not None
        assert meta.inject_key == "goals_inject_enabled"
        assert meta.group == "planning"
        assert meta.priority == 30

    async def test_provide_renders_for_user_scope(self, store):
        """provide：用户会话 scope 返回渲染文本，无目标时 None。"""
        assert await situation._provide("user_qq:1") is None
        await planning_tools.create_goal("目标")
        content = await situation._provide("user_qq:1")
        assert content and "[规划态势]" in content


class TestErrorSelfCorrection:
    async def test_not_found_carries_active_goals(self, store):
        """update_goal 打不存在的 goal_id：错误附活跃目标简报，AI 一次自纠。"""
        raw = await planning_tools.create_goal("存在 的目标", steps="a")
        goal_id = json.loads(raw)["goal"]["goal_id"]

        result = json.loads(await planning_tools.update_goal("ebafea16", goal_status="active"))
        assert result["cause"] == "not_found"
        assert result["active_goals"] == [
            {"goal_id": goal_id, "title": "存在 的目标", "progress": "0/1 步"},
        ]
        assert result["retryable"] is False

    async def test_not_found_without_goals_hints_creation(self, store):
        """无任何活跃目标时 hint 引导创建而非盲目重试。"""
        result = json.loads(await planning_tools.get_goal("ghost"))
        assert result["cause"] == "not_found"
        assert "active_goals" not in result
        assert "create_goal" in result["hint"]

    async def test_delete_goal_not_found_same_context(self, store):
        raw = await planning_tools.create_goal("目标")
        goal_id = json.loads(raw)["goal"]["goal_id"]
        result = json.loads(await planning_tools.delete_goal("ghost"))
        assert result["cause"] == "not_found"
        assert result["active_goals"][0]["goal_id"] == goal_id


class TestUpdateGoalValidation:
    async def test_step_index_out_of_range(self, store):
        """越界步骤索引：PARAM 错误 + 步骤概览（不再静默 no-op 返回 success）。"""
        raw = await planning_tools.create_goal("目标", steps="a|b|c")
        goal_id = json.loads(raw)["goal"]["goal_id"]

        result = json.loads(await planning_tools.update_goal(
            goal_id, step_index=3, step_status="completed",
        ))
        assert result["cause"] == "param"
        assert result["steps"] == ["0: a", "1: b", "2: c"]
        assert "0~2" in result["error"]

    async def test_step_update_on_goal_without_steps(self, store):
        raw = await planning_tools.create_goal("无步骤目标")
        goal_id = json.loads(raw)["goal"]["goal_id"]

        result = json.loads(await planning_tools.update_goal(
            goal_id, step_index=0, step_status="completed",
        ))
        assert result["cause"] == "param"
        assert "没有步骤" in result["error"]

    async def test_invalid_step_status_rejected(self, store):
        raw = await planning_tools.create_goal("目标", steps="a")
        goal_id = json.loads(raw)["goal"]["goal_id"]

        result = json.loads(await planning_tools.update_goal(
            goal_id, step_index=0, step_status="done",
        ))
        assert result["cause"] == "param"
        assert "completed" in result["hint"]

    async def test_goal_status_only_update_still_works(self, store):
        """仅更新整体状态（step_index=-1）的既有语义不受校验影响。"""
        raw = await planning_tools.create_goal("目标", steps="a")
        goal_id = json.loads(raw)["goal"]["goal_id"]

        result = json.loads(await planning_tools.update_goal(goal_id, goal_status="completed"))
        assert result["success"] is True
        assert result["goal"]["status"] == "completed"


class TestSituationLines:
    async def test_active_goal_lines_for_cycle(self, store):
        """自主循环态势收集口径：id: 标题 (done/total 步)。"""
        await planning_tools.create_goal("目标A", steps="a|b")
        lines = await situation.active_goal_lines()
        assert len(lines) == 1
        assert lines[0].endswith("目标A (0/2 步)")
        assert lines[0].split(":")[0].strip()

    async def test_active_goal_briefs_empty_on_clean_store(self, store):
        assert await situation.active_goal_briefs() == []
