"""回执基于已返回的库存/停止事实，不把独白、旧快照或任务受理当作成功。"""

import pytest

from agent.channel.reply_policy import ReplyToolResult
from channels.minecraft.receipts import game_result_receipt


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
