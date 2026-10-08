"""把频道执行契约同步到随插件分发的技能正文。"""

from __future__ import annotations

from pathlib import Path

from channels.minecraft.reply_policy import companion_policy

START = "<!-- BEGIN GENERATED COMPANION CONTRACT -->"
END = "<!-- END GENERATED COMPANION CONTRACT -->"
SKILL_PATH = Path(__file__).resolve().parent.parent / "plugins/minecraft/skills/minecraft-companion/SKILL.md"


def render_contract(source: str) -> str:
    """仅替换唯一的契约块，保留技能的其他说明。"""
    if source.count(START) != 1 or source.count(END) != 1:
        raise ValueError("技能正文必须包含唯一的陪玩契约起止标记")
    prefix, _, rest = source.partition(START)
    if END not in rest:
        raise ValueError("陪玩契约结束标记必须位于开始标记之后")
    _, _, suffix = rest.partition(END)
    return f"{prefix}{START}\n{companion_policy('minecraft').instructions}\n{END}{suffix}"


if __name__ == "__main__":
    SKILL_PATH.write_text(render_contract(SKILL_PATH.read_text(encoding="utf-8")), encoding="utf-8")
