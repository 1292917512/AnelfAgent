"""显式技能标签的正文加载，供消息与动态上下文共用。

Model Experience: skill 标签按确切名称加载技能正文，放入 provider 动态区；缺失或
超出预算时明确提示，不依赖自动匹配分数，不执行技能内的工具或连接 MCP。
Token effect: 无标签时零增量；每轮最多 8 个技能、正文 24K 字符，正文在单次执行内复用。
Cache effect: 只生成动态消息，不改 stable/summary/conversation 前缀或持久化历史。
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from typing import Any

from agent.skills.dependencies import dependency_notice, mcp_binding_notice, mcp_binding_status
from agent.skills.skill_store import Skill, SkillState, SkillStore
from core.activity import ACTIVITY_TEXT_LIMIT, EVENT_ACTIVITY_CONTEXT
from core.event_bus import event_bus
from core.log import log
from core.sanitizer import sanitize_text
from core.tags import etag_all

_MAX_SKILLS = 8
_MAX_CHARS = 24_000


def requested_skill_names(text: str) -> list[str]:
    """只解释显式 skill 标签，不将其他用户标签泛化为工具路由。"""
    return list(dict.fromkeys(value for key, value in etag_all(text) if key == "skill" and value))


class SkillTagContext:
    """单次执行的技能正文缓存；消息选择保留至执行结束，provider 选择逐轮替换。"""

    def __init__(self, store: SkillStore | None) -> None:
        self._store = store
        self._message_names: list[str] = []
        self._loaded: dict[str, Skill | None] = {}
        self._consumed: set[str] = set()

    def _read(self, name: str) -> Skill | None:
        """读取技能定义，不通过标签触发工具执行。"""
        if self._store is None or self._store.normalize_name(name) != name:
            return None
        return self._store.get(name)

    async def build(self, message: str, providers: Sequence[str], *, scope: str) -> list[dict[str, Any]]:
        """按当前输入加载正文；provider 移除的标签不会残留在后续轮次。"""
        incoming = requested_skill_names(message)
        for name in incoming:
            if name not in self._message_names and len(self._message_names) < _MAX_SKILLS:
                self._message_names.append(name)
        provider_names = [name for text in providers for name in requested_skill_names(text)]
        names = list(dict.fromkeys([*self._message_names, *incoming, *provider_names]))
        if not names:
            return []
        messages: list[dict[str, Any]] = []
        facts: list[dict[str, Any]] = []
        remaining = _MAX_CHARS
        for name in names[:_MAX_SKILLS]:
            if self._loaded.get(name) is None:
                try:
                    self._loaded[name] = await asyncio.to_thread(self._read, name)
                except Exception as exc:
                    log(f"指定技能读取失败: {name}: {type(exc).__name__}", "DEBUG", tag="技能")
                    self._loaded[name] = None
            skill = self._loaded[name]
            if skill is None:
                messages.append({"role": "system", "content": f"[指定技能不可用] {name[:64]}；请核对技能名称，不要臆造正文。",
                                 "_layer": "provider", "_source": {"origin": "skill_tag"}})
                continue
            archived = "（已归档，按明确指定读取）" if skill.state is SkillState.ARCHIVED else ""
            heading = f"[调用方指定技能：{skill.name}{archived}]\n"
            states = mcp_binding_status(skill, scope)
            notices = "\n".join(part for part in (mcp_binding_notice(skill, scope, states=states), dependency_notice(skill)) if part)
            available = remaining - len(heading) - len(notices) - 1
            if available <= 100:
                messages.append({"role": "system", "content": f"[指定技能未展开] {skill.name} 超出本轮正文预算，请用 get_skill 读取。",
                                 "_layer": "provider", "_source": {"origin": "skill_tag"}})
                continue
            truncated = len(skill.content) > available
            suffix = "\n（技能正文已截断，请用 get_skill 读取完整内容。）" if truncated else ""
            content = heading + skill.content[:available - len(suffix)] + suffix + ("\n" + notices if notices else "")
            fact = {"name": skill.name, "description": skill.description, "state": skill.state.value,
                    "mode": "read", "score": 1.0, "heading_end": len(heading), "end": len(content),
                    "truncated": truncated, "mcp_status": states}
            messages.append({"role": "system", "content": content, "_layer": "provider",
                             "_source": {"origin": "skill_tag", "skills": [fact]}})
            if name not in self._consumed and self._store is not None:
                try:
                    await asyncio.to_thread(self._store.record_use, name)
                except Exception as exc:
                    log(f"指定技能使用记账失败: {name}: {type(exc).__name__}", "DEBUG", tag="技能")
                facts.append(fact)
                self._consumed.add(name)
            remaining -= len(content)
        if len(names) > _MAX_SKILLS:
            messages.append({"role": "system", "content": f"[指定技能数量限制] 仅展开前 {_MAX_SKILLS} 个，其余请用 get_skill 按需读取。",
                             "_layer": "provider", "_source": {"origin": "skill_tag"}})
        if facts:
            await event_bus.emit(EVENT_ACTIVITY_CONTEXT, {
                "status": "done", "duration_ms": 0, "skills": facts,
                "blocks": [{"layer": "provider", "label": "调用方指定技能",
                            "content": sanitize_text("\n\n".join(item["content"] for item in messages))[:ACTIVITY_TEXT_LIMIT]}],
                "block_count": len(facts), "truncated": any(fact["truncated"] for fact in facts),
            })
        return messages
