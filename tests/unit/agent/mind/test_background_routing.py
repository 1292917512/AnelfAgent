"""后台完成与进度通知：事实持久化、会话路由与持续唤醒。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from agent.mind.background.routing import on_task_alert, on_task_unclaimed
from agent.mind.pfc import PrefrontalCortex


def _fake_mind() -> SimpleNamespace:
    memory = PrefrontalCortex(SimpleNamespace(), SimpleNamespace(router=SimpleNamespace(append=AsyncMock())))
    memory.add_temporary = MagicMock()
    memory.get_adapter_key = lambda scope: "qq"
    return SimpleNamespace(
        pfc=memory,
        try_execute_mind=AsyncMock(),
    )


@pytest.mark.parametrize("scope", ["user_qq:1", "group_qq:2"])
@pytest.mark.parametrize("success", [True, False])
async def test_completion_keeps_waking_without_new_user_input(scope: str, success: bool) -> None:
    mind = _fake_mind()
    queue = mind.pfc.pending_group if scope.startswith("group_") else mind.pfc.pending_user
    append = mind.pfc._conversation_data.router.append

    for index in range(5):
        await on_task_unclaimed(mind, scope, f"优化任务 {index}", f"结果 {index}", success)
        assert append.await_count == index + 1
        assert list(queue.queue) == [scope]
        await asyncio.sleep(0)
        assert mind.try_execute_mind.await_count == index + 1
        mind.pfc.consume_scope_task(scope)

    mind.pfc.add_temporary.assert_not_called()
    saved = append.call_args.kwargs
    assert saved["scope_type"] == scope.split("_", 1)[0]
    assert saved["scope_id"] == scope.split("_", 1)[1]
    assert saved["role"] == "system" and saved["trigger_mind"] is False
    assert ("[后台任务完成]" if success else "[后台任务失败]") in saved["content"]
    assert "结果 4" in saved["content"]
    if not success:
        assert "是否告知用户" in saved["content"] and "是否重试，由你判断" in saved["content"]


async def test_progress_alerts_and_completion_all_reach_model() -> None:
    mind = _fake_mind()
    append = mind.pfc._conversation_data.router.append
    for _ in range(5):
        await on_task_alert(mind, "user_qq:1", "下载任务", "已运行 2000s（预期 1800s）", "ab12cd34")
        await asyncio.sleep(0)

    content = append.call_args.kwargs["content"]
    assert "[后台任务进度报告]" in content and "耗时不代表失败" in content
    assert "由你判断" in content
    assert 'check_background_tasks(task_id="ab12cd34")' in content
    assert 'terminate_background_task(task_id="ab12cd34")' in content
    await on_task_unclaimed(mind, "user_qq:1", "下载任务", "下载完成")
    await asyncio.sleep(0)
    assert append.await_count == 6
    assert mind.try_execute_mind.await_count == 6


@pytest.mark.parametrize("missing_router", [True, False])
async def test_history_unavailable_preserves_notification_and_wakes(missing_router: bool) -> None:
    mind = _fake_mind()
    if missing_router:
        mind.pfc._conversation_data = None
    else:
        mind.pfc._conversation_data.router.append.side_effect = RuntimeError("storage unavailable")

    await on_task_unclaimed(mind, "user_qq:1", "构建任务", "执行失败", success=False)
    await asyncio.sleep(0)
    mind.pfc.add_temporary.assert_called_once()
    assert mind.pfc.add_temporary.call_args.kwargs == {"scope": "user_qq:1"}
    assert "执行失败" in mind.pfc.add_temporary.call_args.args[0]["content"]
    assert list(mind.pfc.pending_user.queue) == ["user_qq:1"]
    mind.try_execute_mind.assert_awaited_once()


@pytest.mark.parametrize("scope", ["_global", "reflect:1", "user_legacy", "user_qq:", "group_"])
async def test_non_conversation_notifications_only_update_global_memory(scope: str) -> None:
    mind = _fake_mind()
    await on_task_unclaimed(mind, scope, "监控任务", "退出码 1", success=False)
    await on_task_alert(mind, scope, "下载任务", "已运行 2000s", "ab12cd34")

    assert mind.pfc.add_temporary.call_count == 2
    mind.pfc._conversation_data.router.append.assert_not_awaited()
    assert mind.pfc.pending_user.is_empty() and mind.pfc.pending_group.is_empty()
    mind.try_execute_mind.assert_not_called()
