"""回执基于已返回的库存/停止事实，不把独白、旧快照或任务受理当作成功。"""

import pytest

from agent.channel.reply_policy import ReplyToolResult
from channels.minecraft.receipts import game_result_receipt, handed_to_game_events
from channels.minecraft.stop_observer import StopCandidateLedger
from core.tool_context import tool_request


def test_inventory_sums_slots_and_exposes_uncollected_materials() -> None:
    text = game_result_receipt([ReplyToolResult("get_inventory", {
        "items": [{"name": "oak_log", "count": 2}, {"name": "oak_log", "count": 1}],
        "crafting": {"cursor": {"name": "stick", "count": 1}, "slots": []},
    })])
    assert "oak_log×3" in text and "尚未全部收回" in text


@pytest.mark.parametrize("payload", [None, {}, {"items": None}, {"items": [{"name": "oak_log", "count": True}]},
                                    {"items": [], "error": "failed"}, {"items": [], "success": False}])
def test_invalid_inventory_is_never_reported_as_empty(payload: object) -> None:
    assert "未确认" in game_result_receipt([ReplyToolResult("get_inventory", payload)])


@pytest.mark.parametrize("stopped,expected", [(True, "当前动作已停止"), (False, "尚未确认完成"), (None, "尚未确认完成")])
def test_stop_requires_explicit_confirmation(stopped: bool | None, expected: str) -> None:
    assert expected in game_result_receipt([ReplyToolResult("cancel_task", {"ok": True, "stopped": stopped})])


@pytest.mark.parametrize("action", ["prepare_item", "manage_supplies", "gather_resources", "mine_resources", "delegate_task", "craft_item"])
def test_actions_invalidate_earlier_inventory_and_do_not_duplicate_terminal_reports(action: str) -> None:
    assert not game_result_receipt([
        ReplyToolResult("get_inventory", {"items": []}), ReplyToolResult(action, {"ok": True}),
    ])


def test_direct_chat_already_sent_and_failed_chat_distinguished() -> None:
    results = [ReplyToolResult("cancel_task", {"ok": True, "stopped": True})]
    assert not game_result_receipt([*results, ReplyToolResult("chat", {"ok": True})])
    assert "已停止" in game_result_receipt([*results, ReplyToolResult("chat", {"error": "offline"})])


def test_stop_receipt_resolves_unmatched_candidate(monkeypatch: pytest.MonkeyPatch) -> None:
    import channels.minecraft.receipts as receipts

    ledger = StopCandidateLedger()
    monkeypatch.setattr(receipts, "stop_candidates", ledger)
    with tool_request("group_minecraft:test", message_id="mc-42") as request:
        ledger.record("mc-42", "停下然后跟我走", "composite")
        assert "当前动作已停止" in game_result_receipt([
            ReplyToolResult("cancel_task", {"ok": True, "stopped": True}),
        ])
        assert ledger.resolve("mc-42", request_id=request.request_id, tool="cancel_task", stopped=True) is None


@pytest.mark.parametrize("tool", ["prepare_item", "manage_supplies", "gather_resources"])
def test_handoff_requires_accepted_current_request(tool: str) -> None:
    with tool_request("group_minecraft:test") as request:
        data = {"id": "task", "actionId": "action", "active": True, "phase": "running",
                "origin": {"requestId": request.request_id, "scope": request.scope}}
        result = ReplyToolResult(tool, data)
        assert handed_to_game_events([result])
        assert "已接单" in game_result_receipt([result])
        for mutation in ({"origin": {}}, {"id": ""}, {"active": False}, {"phase": "blocked"}, {"error": "failure"}):
            assert not handed_to_game_events([ReplyToolResult(tool, {**data, **mutation})])
        assert not handed_to_game_events([result, ReplyToolResult("cancel_task", {"stopped": True})])
    assert not handed_to_game_events([result])
