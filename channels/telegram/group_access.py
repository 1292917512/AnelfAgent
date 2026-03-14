"""群组访问控制策略 -- 参照 openclaw group-access.ts。"""

from __future__ import annotations

from typing import Any, Dict, Optional, Set

from core.log import log


class GroupAccessPolicy:
    """Telegram 群组访问控制。

    支持群组级别的启用/禁用、用户白名单/黑名单、话题级别控制。
    配置通过 ConfigManager 持久化。
    """

    def __init__(self) -> None:
        self._disabled_groups: Set[str] = set()
        self._disabled_topics: Set[str] = set()
        self._group_allowlists: Dict[str, Set[str]] = {}
        self._group_blocklists: Dict[str, Set[str]] = {}
        self._group_require_mention: Dict[str, bool] = {}

    def is_group_enabled(self, chat_id: int, topic_id: Optional[int] = None) -> bool:
        if str(chat_id) in self._disabled_groups:
            return False
        if topic_id is not None and f"{chat_id}:{topic_id}" in self._disabled_topics:
            return False
        return True

    def set_group_enabled(self, chat_id: int, enabled: bool) -> None:
        key = str(chat_id)
        if enabled:
            self._disabled_groups.discard(key)
        else:
            self._disabled_groups.add(key)

    def set_topic_enabled(self, chat_id: int, topic_id: int, enabled: bool) -> None:
        key = f"{chat_id}:{topic_id}"
        if enabled:
            self._disabled_topics.discard(key)
        else:
            self._disabled_topics.add(key)

    def is_sender_allowed(
        self, chat_id: int, sender_id: str, sender_username: str = "",
    ) -> bool:
        key = str(chat_id)
        blocklist = self._group_blocklists.get(key)
        if blocklist and (sender_id in blocklist or sender_username in blocklist):
            return False
        allowlist = self._group_allowlists.get(key)
        if allowlist:
            return sender_id in allowlist or sender_username in allowlist
        return True

    def get_require_mention(self, chat_id: int, default: bool = True) -> bool:
        return self._group_require_mention.get(str(chat_id), default)

    def set_require_mention(self, chat_id: int, value: bool) -> None:
        self._group_require_mention[str(chat_id)] = value

    def add_to_allowlist(self, chat_id: int, user_id: str) -> None:
        key = str(chat_id)
        self._group_allowlists.setdefault(key, set()).add(user_id)

    def remove_from_allowlist(self, chat_id: int, user_id: str) -> None:
        key = str(chat_id)
        if key in self._group_allowlists:
            self._group_allowlists[key].discard(user_id)

    def add_to_blocklist(self, chat_id: int, user_id: str) -> None:
        key = str(chat_id)
        self._group_blocklists.setdefault(key, set()).add(user_id)

    def remove_from_blocklist(self, chat_id: int, user_id: str) -> None:
        key = str(chat_id)
        if key in self._group_blocklists:
            self._group_blocklists[key].discard(user_id)

    def check_access(
        self,
        chat_id: int,
        sender_id: str,
        sender_username: str = "",
        topic_id: Optional[int] = None,
    ) -> tuple[bool, str]:
        """综合检查访问权限。返回 (是否允许, 原因)。"""
        if not self.is_group_enabled(chat_id, topic_id):
            reason = "topic-disabled" if topic_id else "group-disabled"
            return False, reason
        if not self.is_sender_allowed(chat_id, sender_id, sender_username):
            return False, "sender-blocked"
        return True, "allowed"
