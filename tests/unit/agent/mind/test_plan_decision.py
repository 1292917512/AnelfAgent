"""规划决策的执行边界：只管理持久目标，不退化为第二个任务执行循环。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from agent.mind.autonomous import Decision, DecisionType
from agent.mind.decisions import execute_plan


@pytest.mark.asyncio
async def test_plan_uses_goal_management_tools_without_present_plan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("agent.mind.decisions._hb_append", lambda _text: None)
    mind = SimpleNamespace(
        retriever=None,
        char=SimpleNamespace(get_personality_msg=lambda: []),
        reflect=AsyncMock(return_value=""),
    )
    decision = Decision(
        type=DecisionType.PLAN,
        content="通宵修复微信桥并追踪里程碑",
        reason="长任务需要目标追踪",
    )

    await execute_plan(mind, decision)

    call = mind.reflect.await_args
    assert call.kwargs["tool_tags"] == ["planning"]
    assert call.kwargs["extra_blocked_tools"] == {"present_plan"}
    prompt = call.args[0][-1]["content"]
    assert "只创建或推进持久目标" in prompt
    assert "不执行目标中的任务" in prompt
    assert "create_goal" in prompt
    assert "end_reply" not in prompt
