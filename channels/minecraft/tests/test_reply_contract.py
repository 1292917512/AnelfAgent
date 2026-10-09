"""频道提示、技能分发与执行器握手共享同一陪玩契约。"""

import re

import pytest

from agent.channel.reply_policy import ReplyToolResult
from channels.minecraft.reply_policy import COMPANION_GAME_TOOLS, COMPANION_INITIAL_GAME_TOOLS, companion_policy
from core.entity import EntityMetadata, EntityRegistry, EntityType
from core.tool_context import tool_request
from scripts.check_minecraft import _REQUIRED_TOOLS
from scripts.minecraft_contract import END, SKILL_PATH, START, render_contract


def test_distributed_skill_matches_channel_contract() -> None:
    source = SKILL_PATH.read_text(encoding="utf-8")
    assert source == render_contract(source), "请运行 python -m scripts.minecraft_contract 同步技能契约"
    assert "schedule_reminder" not in source


def test_policy_game_tools_are_checked_against_live_registry() -> None:
    tools = set(re.findall(r"`([a-z][a-z_]+)`", companion_policy("custom-server").instructions))
    assert tools == COMPANION_GAME_TOOLS
    assert tools <= _REQUIRED_TOOLS
    assert set(COMPANION_INITIAL_GAME_TOOLS) <= _REQUIRED_TOOLS
    assert companion_policy("custom-server").tool_groups == ("mcp:custom-server",)


def test_policy_uses_only_its_server_registered_game_names(monkeypatch: pytest.MonkeyPatch) -> None:
    tools = [EntityMetadata(
        name=f"custom_server__{name}", entity_type=EntityType.TOOL,
        source="mcp", group="mcp:custom-server", meta={"mcp_original_name": name},
    ) for name in ("get_inventory", "prepare_item")]
    monkeypatch.setattr(EntityRegistry, "get_by_group", lambda group: tools if group == "mcp:custom-server" else [])
    policy = companion_policy("custom-server")
    assert policy.initial_tools is not None
    assert "custom_server__get_inventory" in policy.initial_tools
    assert "get_inventory" not in policy.initial_tools
    assert policy.result_receipt is not None
    assert "oak_log×2" in policy.result_receipt([ReplyToolResult(
        "custom_server__get_inventory", {"items": [{"name": "oak_log", "count": 2}]},
    )])
    assert policy.handoff_to_events is not None
    with tool_request("group_minecraft:test") as request:
        result = ReplyToolResult("custom_server__prepare_item", {
            "id": "task", "actionId": "action", "active": True, "phase": "running",
            "origin": {"requestId": request.request_id, "scope": request.scope},
        })
        assert policy.handoff_to_events([result])
        assert "已接单" in policy.result_receipt([result])


def test_render_preserves_skill_sections_and_is_idempotent() -> None:
    source = f"before\n{START}\nstale rules\n{END}\nafter"
    result = render_contract(source)
    assert result.startswith(f"before\n{START}\n")
    assert result.endswith(f"\n{END}\nafter")
    assert "stale rules" not in result
    assert render_contract(result) == result


@pytest.mark.parametrize("source", ["", f"{END}{START}", f"{START}{START}{END}"])
def test_render_rejects_ambiguous_markers(source: str) -> None:
    with pytest.raises(ValueError):
        render_contract(source)
