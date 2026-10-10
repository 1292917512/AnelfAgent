"""后台交接只采信本次请求的真实受理事实。"""

import pytest

from agent.channel.reply_policy import ReplyToolResult
from channels.minecraft.receipts import handed_to_game_events
from core.tool_context import tool_request


@pytest.mark.parametrize("tool", ["prepare_item", "manage_supplies", "gather_resources"])
def test_handoff_requires_accepted_current_request(tool: str) -> None:
    with tool_request("group_minecraft:test") as request:
        data = {"id": "task", "actionId": "action", "active": True, "phase": "running",
                "origin": {"requestId": request.request_id, "scope": request.scope}}
        result = ReplyToolResult(tool, data)
        assert handed_to_game_events([result])
        for mutation in ({"origin": {}}, {"id": ""}, {"active": False}, {"phase": "blocked"}, {"error": "failure"}):
            assert not handed_to_game_events([ReplyToolResult(tool, {**data, **mutation})])
        assert not handed_to_game_events([result, ReplyToolResult("cancel_task", {"stopped": True})])
    assert not handed_to_game_events([result])
