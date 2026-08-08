"""任务自管理工具与目标注入单元测试。"""

from __future__ import annotations

import json

import pytest

import agent.task.tools as task_tools
from agent.memory.memory_store import MemoryStore
from agent.planning import tools as planning_tools


@pytest.fixture(autouse=True)
def _isolate_task_and_heartbeat_paths(tmp_path, monkeypatch: pytest.MonkeyPatch):
    """隔离任务定义目录与心跳配置文件（conftest 只隔离 ConfigManager，
    ConfigPaths 解析的是真实 config/，必须显式重定向到 tmp）。"""
    import agent.heartbeat.config as hb_config

    monkeypatch.setattr(task_tools, "_tasks_dir", lambda: tmp_path / "tasks")
    monkeypatch.setattr(hb_config, "_CONFIG_PATH", tmp_path / "heartbeat.json")
    monkeypatch.setattr(hb_config, "_instance", None)
    yield
    monkeypatch.setattr(hb_config, "_instance", None)


@pytest.fixture
async def store(tmp_path):
    from agent.planning import tracker

    s = MemoryStore(str(tmp_path / "memory.sqlite3"))
    planning_tools.register_planning_tools(s)
    yield s
    await s.close()
    # 解除全局绑定，避免后续测试复用到已关闭的 store
    tracker._store = None
    planning_tools._store = None


# ==================================================================
# 任务定义 CRUD
# ==================================================================


@pytest.mark.asyncio
async def test_create_task_writes_definition(tmp_path) -> None:
    raw = await task_tools.create_task(
        "daily_brief", "每天早上汇总昨日要点", display_name="每日简报",
        tags="type:reflection,topic:日报",
    )
    data = json.loads(raw)
    assert data["ok"] is True

    path = task_tools._task_path("daily_brief")
    assert path.exists()
    saved = json.loads(path.read_text("utf-8"))
    assert saved["display_name"] == "每日简报"
    assert saved["tags"] == ["type:reflection", "topic:日报"]
    assert saved["tool_tags"] == ["heartbeat"]

    # 重名拒绝
    raw = await task_tools.create_task("daily_brief", "重复")
    assert "error" in json.loads(raw)
    # 非法命名拒绝
    raw = await task_tools.create_task("Bad Name!", "x")
    assert "error" in json.loads(raw)


@pytest.mark.asyncio
async def test_update_task_partial() -> None:
    await task_tools.create_task("t1", "原始 prompt")
    raw = await task_tools.update_task("t1", prompt="新 prompt", enabled="false")
    data = json.loads(raw)
    assert data["ok"] is True
    assert set(data["changed"]) == {"prompt", "enabled"}

    saved = json.loads(task_tools._task_path("t1").read_text("utf-8"))
    assert saved["prompt"] == "新 prompt"
    assert saved["enabled"] is False

    # 无字段更新 → 参数错误；不存在 → NOT_FOUND
    assert "error" in json.loads(await task_tools.update_task("t1"))
    assert "error" in json.loads(await task_tools.update_task("nope", prompt="x"))


@pytest.mark.asyncio
async def test_set_task_schedule_modes() -> None:
    await task_tools.create_task("t2", "做点事")

    raw = await task_tools.set_task_schedule("t2", "heartbeat", every_n_beats=6)
    assert json.loads(raw)["ok"] is True

    from agent.heartbeat.config import get_heartbeat_config
    cfg = get_heartbeat_config()
    sched = cfg.get_schedule("t2")
    assert sched is not None
    assert sched.mode.value == "heartbeat"
    assert sched.every_n_beats == 6

    raw = await task_tools.set_task_schedule("t2", "scheduled", schedule_times="09:00,21:30")
    assert json.loads(raw)["ok"] is True
    cfg = get_heartbeat_config()
    assert cfg.get_schedule("t2").schedule_times == ["09:00", "21:30"]

    # scheduled 缺时间 → 参数错误
    assert "error" in json.loads(await task_tools.set_task_schedule("t2", "scheduled"))
    # 不存在任务 → NOT_FOUND
    assert "error" in json.loads(await task_tools.set_task_schedule("ghost", "manual"))

    # manual → 移除调度
    raw = await task_tools.set_task_schedule("t2", "manual")
    assert json.loads(raw)["ok"] is True
    cfg = get_heartbeat_config()
    assert cfg.get_schedule("t2") is None


@pytest.mark.asyncio
async def test_delete_task_removes_schedule() -> None:
    await task_tools.create_task("t3", "做点事")
    await task_tools.set_task_schedule("t3", "heartbeat", every_n_beats=3)

    raw = await task_tools.delete_task("t3")
    data = json.loads(raw)
    assert data["ok"] is True
    assert data["schedule_removed"] is True
    assert not task_tools._task_path("t3").exists()

    from agent.heartbeat.config import get_heartbeat_config
    assert get_heartbeat_config().get_schedule("t3") is None


# ==================================================================
# 目标注入
# ==================================================================


@pytest.mark.asyncio
async def test_build_goals_injection(store) -> None:
    raw = await planning_tools.create_goal("整理周报", steps="收集|汇总|发送")
    goal = json.loads(raw)["goal"]

    content = await planning_tools.build_goals_injection(store)
    assert "[系统注入·活跃目标]" in content
    assert goal["goal_id"] in content
    assert "0/3 步" in content

    # 完成态目标不再注入
    await planning_tools.update_goal(goal["goal_id"], goal_status="completed")
    content = await planning_tools.build_goals_injection(store)
    assert content == ""


@pytest.mark.asyncio
async def test_goal_entries_scope_isolation(store) -> None:
    """present_plan 产出的对话内计划按 scope 隔离，长期目标全局可见。"""
    from agent.planning import tracker

    await tracker.submit_plan("user_qq:1", "会话内计划", tracker.parse_steps("步骤一"))
    await planning_tools.create_goal("长期目标")

    own = await planning_tools.collect_active_goal_entries(store, scope="user_qq:1")
    titles = {g["title"] for g in own}
    assert titles == {"会话内计划", "长期目标"}

    other = await planning_tools.collect_active_goal_entries(store, scope="user_qq:2")
    titles = {g["title"] for g in other}
    assert titles == {"长期目标"}


@pytest.mark.asyncio
async def test_goal_tags_linkage(store) -> None:
    """goal:{id} 标签：创建时打上，get_goal 可反查关联记忆。"""
    raw = await planning_tools.create_goal("有关联的目标")
    goal = json.loads(raw)["goal"]

    from agent.memory.memory_types import MemoryEntry, MemoryType
    await store.add(MemoryEntry(
        memory_type=MemoryType.SEMANTIC, content="与该目标相关的事实",
        tags=[f"goal:{goal['goal_id']}", "type:fact"],
    ))

    raw = await planning_tools.get_goal(goal["goal_id"])
    data = json.loads(raw)
    assert data["success"] is True
    assert data["related_memory_count"] == 1
