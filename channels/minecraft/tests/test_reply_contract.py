"""频道提示、技能分发与执行器握手共享同一陪玩契约。"""

import re

import pytest

from channels.minecraft.reply_policy import COMPANION_GAME_TOOLS, COMPANION_INITIAL_GAME_TOOLS, companion_policy
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
