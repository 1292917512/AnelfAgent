"""QQ 群通知触发思考测试：分档决策、群角色缓存与 notice 消息契约（不触网）。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock

import pytest

from agent.channel.schemas import MessageKind
from channels.qq.parser import (
    NOTICE_CATEGORY_INTERACTION,
    NOTICE_CATEGORY_MEMBERSHIP,
    NOTICE_CATEGORY_MODERATION,
    notice_category,
    parse_event_async,
)
from channels.qq.transport import QQTransport


class _ChannelStub:
    """QQTransport 的最小频道替身：按表返回配置、收集 on_message 消息。"""

    def __init__(self, cfg: Optional[Dict[str, Any]] = None, self_id: str = "10086") -> None:
        self._self_id = self_id
        self._cfg_map = cfg or {}
        self.received: List[Any] = []

    def _check_whitelist(self, data: Dict[str, Any]) -> bool:
        return True

    def _cfg(self, key: str, default: Any = None) -> Any:
        return self._cfg_map.get(key, default)

    async def on_message(self, message: Any) -> None:
        self.received.append(message)


def _make_transport(
    cfg: Optional[Dict[str, Any]] = None,
    role: Optional[str] = None,
) -> tuple[QQTransport, _ChannelStub, AsyncMock]:
    ch = _ChannelStub(cfg=cfg)
    transport = QQTransport(ch)  # type: ignore[arg-type]
    response: Optional[Dict[str, Any]] = (
        {"retcode": 0, "data": {"role": role}} if role is not None else None
    )
    transport.call_api_raw = AsyncMock(return_value=response)  # type: ignore[method-assign]
    return transport, ch, transport.call_api_raw  # type: ignore[return-value]


def _notice(notice_type: str, group_id: int = 110, **extra: Any) -> Dict[str, Any]:
    return {
        "post_type": "notice",
        "notice_type": notice_type,
        "sub_type": extra.pop("sub_type", ""),
        "group_id": group_id,
        "user_id": 12345,
        "operator_id": 999,
        "self_id": 10086,
        "time": 1759900000,
        **extra,
    }


class TestNoticeCategory:
    """通知事件重要性分档表。"""

    @pytest.mark.parametrize("notice_type,expected", [
        ("group_increase", NOTICE_CATEGORY_MEMBERSHIP),
        ("group_decrease", NOTICE_CATEGORY_MEMBERSHIP),
        ("group_ban", NOTICE_CATEGORY_MODERATION),
        ("group_admin", NOTICE_CATEGORY_MODERATION),
        ("group_recall", NOTICE_CATEGORY_MODERATION),
        ("group_upload", NOTICE_CATEGORY_INTERACTION),
        ("friend_add", NOTICE_CATEGORY_INTERACTION),
        ("essence", NOTICE_CATEGORY_INTERACTION),
    ])
    def test_category_table(self, notice_type: str, expected: str) -> None:
        assert notice_category(notice_type, "") == expected

    def test_poke_sub_type(self) -> None:
        assert notice_category("notify", "poke") == NOTICE_CATEGORY_INTERACTION

    def test_notify_other_sub_type_falls_back(self) -> None:
        assert notice_category("notify", "lucky_king") == NOTICE_CATEGORY_INTERACTION


class TestNoticeMessageContract:
    """notice 解析产物契约：kind=EVENT（平台事件，非真人聊天）。"""

    async def test_group_increase_kind_event(self) -> None:
        message = await parse_event_async(_notice("group_increase"))
        assert message is not None
        assert message.kind == MessageKind.EVENT
        assert "加入了群聊" in message.content
        assert message.is_to_me is False

    async def test_group_recall_kind_event(self) -> None:
        message = await parse_event_async(_notice("group_recall"))
        assert message is not None
        assert message.kind == MessageKind.EVENT
        assert "撤回" in message.content

    async def test_poke_bot_is_to_me(self) -> None:
        message = await parse_event_async(_notice("notify", sub_type="poke", target_id=10086))
        assert message is not None
        assert message.is_to_me is True


class TestDecideNoticeTrigger:
    """group_notice_trigger 三档决策矩阵。"""

    async def test_off_disables_all(self) -> None:
        transport, _, _ = _make_transport(cfg={"group_notice_trigger": "off"})
        for notice_type in ("group_increase", "group_recall", "group_upload"):
            assert await transport._decide_notice_trigger(_notice(notice_type)) is False

    async def test_all_enables_all(self) -> None:
        transport, _, _ = _make_transport(cfg={"group_notice_trigger": "all"})
        for notice_type in ("group_increase", "group_recall", "group_upload"):
            assert await transport._decide_notice_trigger(_notice(notice_type)) is True

    async def test_auto_membership_always_triggers(self) -> None:
        transport, _, raw_mock = _make_transport(cfg={})
        assert await transport._decide_notice_trigger(_notice("group_increase")) is True
        assert await transport._decide_notice_trigger(_notice("group_decrease")) is True
        # 成员变动不依赖角色，不应触发 API 查询
        raw_mock.assert_not_called()

    async def test_auto_interaction_never_triggers(self) -> None:
        transport, _, _ = _make_transport(cfg={})
        assert await transport._decide_notice_trigger(_notice("group_upload")) is False
        assert await transport._decide_notice_trigger(
            _notice("notify", sub_type="poke", target_id=22222)
        ) is False

    @pytest.mark.parametrize("role,expected", [
        ("owner", True),
        ("admin", True),
        ("member", False),
        ("", False),
    ])
    async def test_auto_moderation_by_role(self, role: str, expected: bool) -> None:
        transport, _, _ = _make_transport(cfg={}, role=role)
        got = await transport._decide_notice_trigger(_notice("group_recall"))
        assert got is expected

    async def test_auto_moderation_api_failure_degrades_false(self) -> None:
        transport, _, _ = _make_transport(cfg={}, role=None)
        assert await transport._decide_notice_trigger(_notice("group_ban")) is False

    async def test_moderation_without_group_id_no_trigger(self) -> None:
        transport, _, _ = _make_transport(cfg={})
        data = _notice("group_recall")
        data.pop("group_id")
        assert await transport._decide_notice_trigger(data) is False


class TestGroupAdminCache:
    """群主/管理员判定缓存：TTL 内只查一次、过期重查。"""

    async def test_cache_within_ttl(self) -> None:
        transport, _, raw_mock = _make_transport(cfg={}, role="admin")
        assert await transport._is_group_admin("110") is True
        assert await transport._is_group_admin("110") is True
        assert raw_mock.call_count == 1

    async def test_cache_expiry_requeries(self) -> None:
        transport, _, _ = _make_transport(cfg={}, role="admin")
        assert await transport._is_group_admin("110") is True
        # 人工老化缓存，模拟 TTL 过期
        transport._group_admin_cache["110"] = (True, -1e9)
        assert await transport._is_group_admin("110") is True
        assert transport.call_api_raw.call_count == 2  # type: ignore[attr-defined]

    async def test_failure_not_cached_recovers_immediately(self) -> None:
        """角色查询失败不固化降级结果：恢复后下一条管理类通知立即生效。"""
        transport, _, raw_mock = _make_transport(cfg={}, role=None)
        assert await transport._is_group_admin("110") is False
        assert transport._group_admin_cache == {}
        raw_mock.return_value = {"retcode": 0, "data": {"role": "admin"}}
        assert await transport._is_group_admin("110") is True

    async def test_empty_self_id_short_circuits(self) -> None:
        ch = _ChannelStub(cfg={}, self_id="")
        transport = QQTransport(ch)  # type: ignore[arg-type]
        transport.call_api_raw = AsyncMock()  # type: ignore[method-assign]
        assert await transport._is_group_admin("110") is False
        transport.call_api_raw.assert_not_called()  # type: ignore[attr-defined]


class TestProcessEventTriggerFlow:
    """_process_event 集成：require_mention 下 notice 分流、普通消息不受影响。"""

    async def test_membership_notice_triggers_under_require_mention(self) -> None:
        transport, ch, _ = _make_transport(cfg={"require_mention": True})
        await transport._process_event(_notice("group_increase"))
        assert len(ch.received) == 1
        assert ch.received[0].trigger_mind is True

    async def test_recall_notice_as_admin_triggers(self) -> None:
        transport, ch, _ = _make_transport(
            cfg={"require_mention": True}, role="admin"
        )
        await transport._process_event(_notice("group_recall"))
        assert len(ch.received) == 1
        assert ch.received[0].trigger_mind is True

    async def test_recall_notice_as_member_not_trigger(self) -> None:
        transport, ch, _ = _make_transport(
            cfg={"require_mention": True}, role="member"
        )
        await transport._process_event(_notice("group_recall"))
        assert len(ch.received) == 1
        assert ch.received[0].trigger_mind is False

    async def test_normal_group_message_still_muted(self) -> None:
        transport, ch, _ = _make_transport(cfg={"require_mention": True})
        data = {
            "post_type": "message",
            "message_type": "group",
            "group_id": 110,
            "user_id": 12345,
            "self_id": 10086,
            "message": [{"type": "text", "data": {"text": "群员闲聊"}}],
            "raw_message": "群员闲聊",
            "time": 1759900000,
        }
        await transport._process_event(data)
        assert len(ch.received) == 1
        assert ch.received[0].trigger_mind is False
        assert ch.received[0].kind == MessageKind.CHAT

    async def test_mentioned_message_unchanged(self) -> None:
        transport, ch, _ = _make_transport(cfg={"require_mention": True})
        data = {
            "post_type": "message",
            "message_type": "group",
            "group_id": 110,
            "user_id": 12345,
            "self_id": 10086,
            "message": [{"type": "at", "data": {"qq": 10086}}, {"type": "text", "data": {"text": " 你好"}}],
            "raw_message": "[CQ:at,qq=10086] 你好",
            "time": 1759900000,
        }
        await transport._process_event(data)
        assert len(ch.received) == 1
        assert ch.received[0].trigger_mind is True

    async def test_no_require_mention_notice_still_triggers(self) -> None:
        """require_mention=False 时消息本就全触发，notice 不回归。"""
        transport, ch, _ = _make_transport(cfg={"require_mention": False})
        await transport._process_event(_notice("group_upload"))
        assert len(ch.received) == 1
        assert ch.received[0].trigger_mind is True
