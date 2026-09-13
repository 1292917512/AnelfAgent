"""飞书会话 scope 归一测试（不触网）。

回归背景：飞书私聊的发送目标是 chat_id（oc_），而入站会话 scope 为
``user_feishu:{open_id}#{chat_id}``。发送/记录路径不做归一时，AI 主动
发送的 assistant 历史写到 ``user_feishu:{chat_id}``，同一会话撕裂成两个
scope（AI 看不到自己说过什么，反复重发）。
"""

from __future__ import annotations

import pytest

from channels.feishu import state as feishu_state
from channels.feishu.adapter import FeishuChannel

_PEER = "ou_04b1d18f78b663214f05414d8d8c5e23"
_P2P_CHAT = "oc_c6ea83980a39e44e6522ca588e9317df"
_GROUP_CHAT = "oc_group111"


@pytest.fixture()
def channel(tmp_path, monkeypatch) -> FeishuChannel:
    """隔离数据目录的频道实例（known_chats 持久化落到 tmp_path）。"""
    monkeypatch.setattr(feishu_state, "feishu_data_dir", lambda: str(tmp_path))
    return FeishuChannel()


class TestKnownChatRegistry:
    def test_p2p_peer_recorded(self, channel: FeishuChannel) -> None:
        channel._on_chat_seen(_P2P_CHAT, "p2p", _PEER)
        known = channel._known_chats[_P2P_CHAT]
        assert known["type"] == "p2p"
        assert known["peer_open_id"] == _PEER

    def test_group_has_no_peer(self, channel: FeishuChannel) -> None:
        channel._on_chat_seen(_GROUP_CHAT, "group", "ou_someone")
        known = channel._known_chats[_GROUP_CHAT]
        assert known["type"] == "group"
        assert "peer_open_id" not in known

    def test_registry_persisted_and_reloaded(self, channel: FeishuChannel, tmp_path) -> None:
        channel._on_chat_seen(_P2P_CHAT, "p2p", _PEER)
        loaded = feishu_state.load_known_chats()
        assert loaded[_P2P_CHAT]["peer_open_id"] == _PEER
        assert loaded[_P2P_CHAT]["type"] == "p2p"

    def test_unchanged_seen_does_not_rewrite(self, channel: FeishuChannel, tmp_path) -> None:
        channel._on_chat_seen(_P2P_CHAT, "p2p", _PEER)
        path = tmp_path / "known_chats.json"
        mtime = path.stat().st_mtime_ns
        channel._on_chat_seen(_P2P_CHAT, "p2p", _PEER)
        assert path.stat().st_mtime_ns == mtime

    def test_corrupt_state_file_loads_empty(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr(feishu_state, "feishu_data_dir", lambda: str(tmp_path))
        (tmp_path / "known_chats.json").write_text("{not json", encoding="utf-8")
        assert feishu_state.load_known_chats() == {}


class TestIsKnownGroup:
    def test_group_true(self, channel: FeishuChannel) -> None:
        channel._on_chat_seen(_GROUP_CHAT, "group", "ou_someone")
        assert channel.is_known_group(_GROUP_CHAT) is True

    def test_p2p_false(self, channel: FeishuChannel) -> None:
        channel._on_chat_seen(_P2P_CHAT, "p2p", _PEER)
        assert channel.is_known_group(_P2P_CHAT) is False

    def test_unknown_false(self, channel: FeishuChannel) -> None:
        assert channel.is_known_group("oc_never_seen") is False


class TestConversationScopeForTarget:
    def test_p2p_chat_id_maps_to_canonical_scope(self, channel: FeishuChannel) -> None:
        """AI 以 chat_id 为私聊目标发送时，记录归并到入站消息的规范 scope。"""
        channel._on_chat_seen(_P2P_CHAT, "p2p", _PEER)
        assert channel.conversation_scope_for_target(_P2P_CHAT, "private") == (
            "user", f"feishu:{_PEER}#{_P2P_CHAT}",
        )

    def test_open_id_maps_via_reverse_lookup(self, channel: FeishuChannel) -> None:
        """以 open_id 为私聊目标时经反向映射归并到同一规范 scope。"""
        channel._on_chat_seen(_P2P_CHAT, "p2p", _PEER)
        assert channel.conversation_scope_for_target(_PEER, "private") == (
            "user", f"feishu:{_PEER}#{_P2P_CHAT}",
        )

    def test_group_target_uses_generic_rule(self, channel: FeishuChannel) -> None:
        channel._on_chat_seen(_GROUP_CHAT, "group", "ou_someone")
        assert channel.conversation_scope_for_target(_GROUP_CHAT, "group") is None

    def test_unknown_target_falls_back(self, channel: FeishuChannel) -> None:
        assert channel.conversation_scope_for_target("oc_never_seen", "private") is None
        assert channel.conversation_scope_for_target("ou_never_seen", "private") is None

    def test_group_chat_id_not_misclassified_as_private(self, channel: FeishuChannel) -> None:
        """群聊 chat_id 不会被当成私聊映射（群/私以登记类型为准）。"""
        channel._on_chat_seen(_GROUP_CHAT, "group", "ou_someone")
        assert channel.conversation_scope_for_target(_GROUP_CHAT, "private") is None
