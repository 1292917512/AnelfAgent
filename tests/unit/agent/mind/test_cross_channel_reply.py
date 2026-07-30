"""跨频道并发回复不割裂的链路验证。

场景：同一时刻 A 频道（QQ 群）与 B 频道（WebUI 私聊）各来一条消息——
两个 scope 必须各自独立入队、各自解析出正确的频道路由，互不串台。
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

from agent.messages.presets import MessageGroupUser, MessageUser
from agent.mind.prefrontal_cortex import PrefrontalCortex
from agent.mind.tools.decision_executor import resolve_reply_target


def _pfc() -> PrefrontalCortex:
    entity = SimpleNamespace(
        add_conversations_num=lambda: 0,
        reset_conversations_num=lambda: None,
        personality={},
        uid=0,
    )
    return PrefrontalCortex(
        everything_data=SimpleNamespace(get_anything=AsyncMock(return_value=entity)),
    )


class TestCrossChannelIsolation:
    async def test_scopes_bucketed_by_adapter(self) -> None:
        """两频道消息进入各自的 scope 桶（adapter 维度隔离）。"""
        pfc = _pfc()
        await pfc.add_task(MessageGroupUser(
            uid="u1", group_id="100", adapter_key="qq", text_content="群消息",
        ))
        await pfc.add_task(MessageUser(
            uid="web_user", adapter_key="webui", text_content="私聊消息",
        ))

        scopes = [s for s, _u, _g, _p in pfc.peek_all_tasks()]
        assert "group_qq:100" in scopes
        assert "user_webui:web_user" in scopes
        # 群聊进 pending_group，私聊进 pending_user，互不混杂
        assert "group_qq:100" in pfc.pending_group.seen
        assert "user_webui:web_user" in pfc.pending_user.seen

    async def test_reply_targets_resolve_own_channel(self) -> None:
        """两个 scope 的回复目标各自解析出正确频道（不依赖共享路由表）。"""
        pfc = _pfc()
        await pfc.add_task(MessageGroupUser(
            uid="u1", group_id="100", adapter_key="qq", text_content="群消息",
        ))
        await pfc.add_task(MessageUser(
            uid="web_user", adapter_key="webui", text_content="私聊消息",
        ))
        mind = SimpleNamespace(pfc=pfc, _active_scopes=set())

        msg_group = resolve_reply_target(mind, "group_qq:100")
        assert msg_group is not None
        assert msg_group.adapter_key == "qq"
        assert msg_group.group_id == "100" or msg_group.group_id == 100

        # 第一个 scope 消费后，第二个 scope 不受影响
        msg_user = resolve_reply_target(mind, "user_webui:web_user")
        assert msg_user is not None
        assert msg_user.adapter_key == "webui"

        assert pfc.peek_all_tasks() == []

    async def test_scope_embedded_adapter_survives_routing_table_loss(self) -> None:
        """路由表（_task_adapter_keys）缺失时，scope 内嵌 adapter 仍能正确路由。

        覆盖进程重启后 replay/恢复场景：内存路由表为空，但 scope 自带频道信息。
        """
        pfc = _pfc()
        # 直接入队（绕过 add_task 的路由表登记）
        pfc.pending_user.append("user_telegram:456")
        mind = SimpleNamespace(pfc=pfc, _active_scopes=set())

        msg = resolve_reply_target(mind, "user_telegram:456")
        assert msg is not None
        assert msg.adapter_key == "telegram"

    async def test_cross_channel_snapshot_uses_adapter_scopes(self) -> None:
        """跨频道快照按新格式 scope 记录（A 频道活跃可被 B 频道感知）。"""
        from agent.mind.cross_channel import update_channel_snapshot

        mind = SimpleNamespace(_channel_snapshots={})
        mind._resolve_entity_scope = lambda a: a.entity_scope if a else ""
        update_channel_snapshot(
            mind, MessageUser(uid="123", adapter_key="qq", text_content="在QQ说话")
        )
        update_channel_snapshot(
            mind, MessageUser(uid="web_user", adapter_key="webui", text_content="在Web说话")
        )

        assert "user_qq:123" in mind._channel_snapshots["qq"].active_scopes
        assert "user_webui:web_user" in mind._channel_snapshots["webui"].active_scopes
        # 两频道快照互相独立
        assert "user_webui:web_user" not in mind._channel_snapshots["qq"].active_scopes
