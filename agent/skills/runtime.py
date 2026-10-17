"""技能运行依赖与按会话隔离的显式调用请求。"""

from __future__ import annotations

from dataclasses import dataclass, field

from agent.skills.curator import SkillCurator
from agent.skills.gesture import parse_skill_gesture
from agent.skills.skill_matcher import SkillMatcher
from agent.skills.skill_store import SkillStore
from core.config import get_config_bool
from core.log import log
from core.tags import strip_message_meta_tags


@dataclass
class SkillRuntime:
    """共享技能服务，登记外部消息的显式请求并在召回时一次性消费。"""

    store: SkillStore
    matcher: SkillMatcher
    curator: SkillCurator
    _pending_requests: dict[str, list[str]] = field(default_factory=dict, init=False)

    def observe_request(self, text: str, scope: str) -> None:
        """识别可信外部消息开头的 /name，每个会话最多保留三个不同请求。"""
        try:
            if not get_config_bool("skill_user_gesture_enabled", True):
                return
            name = parse_skill_gesture(strip_message_meta_tags(text).strip())
            if not name:
                return
            pending = self._pending_requests.setdefault(scope, [])
            if len(pending) < 3 and name not in pending:
                pending.append(name)
                log(f"识别到技能手势 /{name}，将在召回时确定性注入: {scope}", "DEBUG", tag="技能")
        except Exception as exc:
            log(f"识别显式技能调用失败: {exc}", "DEBUG", tag="技能")

    def consume_requests(self, scope: str) -> list[str]:
        """取出并清除指定会话的待调用技能，空会话不消费请求。"""
        return self._pending_requests.pop(scope, []) if scope else []
