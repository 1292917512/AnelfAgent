"""频道工具桥接（agent.channel.tool_bridge）单元测试。

覆盖：@channel_tool 标记扫描、通用能力工具合并/路由、特有工具命名与 tag、
敏感门控、注册/注销生命周期、forward_message 别名映射。
"""

from __future__ import annotations

import json
from typing import Any, Set

import pytest

import agent.channel.manager as mgr
from agent.channel.base import BaseChannel, ChannelConfig
from agent.channel.channel_types import ChannelCapability
from agent.channel.context import bind_current_channel
from agent.channel.schemas import (
    ChannelInfo, ChannelUser, HealthStatus, SendRequest, SendResponse,
)
from agent.channel.tool_bridge import (
    channel_tool, register_channel_tools, unregister_channel_tools,
    _collect_marked_methods, _sensitive_check,
)
from core.config import ConfigManager
from core.entity import EntityRegistry


class _FakeConfig(ChannelConfig):
    pass


class FakeChannel(BaseChannel[_FakeConfig]):
    """测试频道：声明部分能力并标记特有/通用方法。"""

    channel_id = "fake"
    display_name = "Fake"
    capabilities: Set[ChannelCapability] = {
        ChannelCapability.SEND_TEXT,
        ChannelCapability.DELETE_MESSAGE,
        ChannelCapability.BAN_USER,
        ChannelCapability.FORWARD_MESSAGE,
    }
    _Configs = _FakeConfig

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    async def forward_message(self, request: SendRequest) -> SendResponse:
        return SendResponse(success=True, message_id="m1")

    async def get_self_info(self) -> ChannelUser:
        return ChannelUser(user_id="bot", user_name="bot")

    async def get_user_info(self, user_id: str, channel_id: str) -> ChannelUser:
        return ChannelUser(user_id=user_id, user_name=user_id)

    async def get_channel_info(self, channel_id: str) -> ChannelInfo:
        return ChannelInfo(channel_id=channel_id)

    async def health_check(self) -> HealthStatus:
        return HealthStatus(healthy=True)

    @channel_tool(description="撤回消息")
    async def delete_message(self, chat_id: str, message_id: str, **kwargs: Any) -> str:
        """撤回消息。"""
        return json.dumps({"success": True, "deleted": message_id, "channel_type": kwargs.get("channel_type")})

    @channel_tool(sensitive=True)
    async def ban_user(self, chat_id: str, user_id: str, duration: int = 60, **kwargs: Any) -> str:
        """禁言用户。

        Args:
            chat_id: 群号
            user_id: 用户 ID
            duration: 禁言秒数
        """
        return json.dumps({"success": True, "banned": user_id, "duration": duration})

    @channel_tool()
    async def forward_msg(self, chat_id: str, from_chat_id: str, message_id: str, **kwargs: Any) -> str:
        """转发消息。"""
        return json.dumps({"success": True})

    @channel_tool()
    async def do_special(self, target: str) -> str:
        """特有方法。

        Args:
            target: 目标
        """
        return json.dumps({"success": True, "target": target})


class FakeChannel2(FakeChannel):
    """第二频道：同名能力实现，验证通用工具合并与多支持者优先级。"""

    channel_id = "fake2"
    capabilities: Set[ChannelCapability] = {
        ChannelCapability.SEND_TEXT,
        ChannelCapability.DELETE_MESSAGE,
    }


@pytest.fixture()
def channel_manager():
    """提供干净的 ChannelManager 单例与桥接状态，并在测试后清理。"""
    old = mgr._channel_manager
    mgr._channel_manager = mgr.ChannelManager()
    cm = mgr.get_channel_manager()
    created: list[FakeChannel] = []
    yield cm, created
    for ch in created:
        unregister_channel_tools(ch.channel_id)
    mgr._channel_manager = old


def _make(cm: Any, created: list, cls: Any = FakeChannel) -> Any:
    ch = cls.__new__(cls)
    BaseChannel.__init__(ch)
    created.append(ch)
    cm.register(ch)
    return ch


def _registered(name: str) -> bool:
    return name in EntityRegistry.get_all_names()


class TestCollectMarked:
    def test_collects_decorated_methods(self, channel_manager) -> None:
        cm, created = channel_manager
        ch = _make(cm, created)
        marked = _collect_marked_methods(ch)
        for name in ("delete_message", "ban_user", "forward_msg", "do_special"):
            assert name in marked

    def test_sensitive_meta(self, channel_manager) -> None:
        cm, created = channel_manager
        ch = _make(cm, created)
        marked = _collect_marked_methods(ch)
        assert marked["ban_user"][1].sensitive is True
        assert marked["do_special"][1].sensitive is False


class TestSpecificTools:
    def test_named_with_channel_prefix(self, channel_manager) -> None:
        cm, created = channel_manager
        _make(cm, created)
        assert _registered("fake_do_special")

    def test_schema_has_no_channel_id(self, channel_manager) -> None:
        cm, created = channel_manager
        _make(cm, created)
        schemas = {s["function"]["name"]: s for s in EntityRegistry.get_tool_schemas()}
        props = schemas["fake_do_special"]["function"]["parameters"]["properties"]
        assert "target" in props
        assert "channel_id" not in props

    def test_tagged_with_channel_id(self, channel_manager) -> None:
        cm, created = channel_manager
        _make(cm, created)
        hit = [s["function"]["name"] for s in EntityRegistry.get_tool_schema_by_tags(["fake"])]
        assert "fake_do_special" in hit

    @pytest.mark.asyncio
    async def test_execute_bound_method(self, channel_manager) -> None:
        cm, created = channel_manager
        _make(cm, created)
        res = json.loads(await EntityRegistry.execute_tool("fake_do_special", '{"target":"x"}'))
        assert res["success"] is True
        assert res["target"] == "x"


class TestCommonTools:
    def test_capability_tool_registered(self, channel_manager) -> None:
        cm, created = channel_manager
        _make(cm, created)
        for name in ("delete_message", "ban_user", "forward_message"):
            assert _registered(name), name

    def test_capability_tag(self, channel_manager) -> None:
        cm, created = channel_manager
        _make(cm, created)
        hit = [s["function"]["name"] for s in EntityRegistry.get_tool_schema_by_tags(["delete_message"])]
        assert "delete_message" in hit

    def test_schema_has_channel_id_first(self, channel_manager) -> None:
        cm, created = channel_manager
        _make(cm, created)
        schemas = {s["function"]["name"]: s for s in EntityRegistry.get_tool_schemas()}
        params = schemas["ban_user"]["function"]["parameters"]
        props = list(params["properties"])
        assert props[0] == "channel_id"
        assert "duration" in props
        assert set(params["required"]) == {"chat_id", "user_id"}

    def test_alias_forward_message_maps_to_forward_msg(self, channel_manager) -> None:
        cm, created = channel_manager
        _make(cm, created)
        schemas = {s["function"]["name"]: s for s in EntityRegistry.get_tool_schemas()}
        props = schemas["forward_message"]["function"]["parameters"]["properties"]
        assert "from_chat_id" in props

    def test_undeclared_capability_not_registered(self, channel_manager) -> None:
        """FakeChannel 未声明 SEND_VIDEO，不应产生 send_video 工具或 fake_send_video。"""
        cm, created = channel_manager
        _make(cm, created)
        assert not _registered("send_video")
        assert not _registered("fake_send_video")

    @pytest.mark.asyncio
    async def test_route_via_contextvar(self, channel_manager) -> None:
        cm, created = channel_manager
        _make(cm, created)
        bind_current_channel("fake")
        res = json.loads(await EntityRegistry.execute_tool(
            "delete_message", '{"chat_id":"group:123","message_id":"9"}'))
        assert res["success"] is True
        assert res["deleted"] == "9"
        # group: 前缀解析后 channel_type 自动注入
        assert res["channel_type"] == "group"

    @pytest.mark.asyncio
    async def test_route_explicit_channel_id(self, channel_manager) -> None:
        cm, created = channel_manager
        _make(cm, created)
        _make(cm, created, FakeChannel2)
        res = json.loads(await EntityRegistry.execute_tool(
            "delete_message", '{"channel_id":"fake2","chat_id":"1","message_id":"2"}'))
        assert res["success"] is True
        assert res["channel_id"] == "fake2"

    @pytest.mark.asyncio
    async def test_route_no_channel_error(self, channel_manager) -> None:
        cm, created = channel_manager
        _make(cm, created)
        res = json.loads(await EntityRegistry.execute_tool(
            "delete_message", '{"chat_id":"1","message_id":"2"}'))
        assert res["success"] is False
        assert "fake" in res["supporting_channels"]

    @pytest.mark.asyncio
    async def test_route_unsupported_channel_error(self, channel_manager) -> None:
        cm, created = channel_manager
        _make(cm, created)
        _make(cm, created, FakeChannel2)
        res = json.loads(await EntityRegistry.execute_tool(
            "ban_user", '{"channel_id":"fake2","chat_id":"1","user_id":"2"}'))
        assert res["success"] is False
        assert res["supporting_channels"] == ["fake"]


class TestSensitiveGate:
    def test_default_enabled(self) -> None:
        ConfigManager.set("channel_tools_allow_sensitive", True)
        assert _sensitive_check() is True

    def test_disabled_via_config(self) -> None:
        ConfigManager.set("channel_tools_allow_sensitive", False)
        try:
            assert _sensitive_check() is False
        finally:
            ConfigManager.set("channel_tools_allow_sensitive", True)

    @pytest.mark.asyncio
    async def test_sensitive_tool_filtered_when_disabled(self, channel_manager) -> None:
        cm, created = channel_manager
        _make(cm, created)
        ConfigManager.set("channel_tools_allow_sensitive", False)
        try:
            active = await EntityRegistry.get_active_tools(["ban_user", "delete_message"])
            names = [t.name for t in active]
            assert "ban_user" not in names
            assert "delete_message" in names
        finally:
            ConfigManager.set("channel_tools_allow_sensitive", True)


class TestLifecycle:
    def test_unregister_removes_specific(self, channel_manager) -> None:
        cm, created = channel_manager
        _make(cm, created)
        assert _registered("fake_do_special")
        unregister_channel_tools("fake")
        assert not _registered("fake_do_special")

    def test_unregister_rebuilds_common(self, channel_manager) -> None:
        cm, created = channel_manager
        _make(cm, created)
        _make(cm, created, FakeChannel2)
        unregister_channel_tools("fake2")
        assert _registered("delete_message")  # fake 仍支持
        unregister_channel_tools("fake")
        assert not _registered("delete_message")

    def test_register_idempotent(self, channel_manager) -> None:
        cm, created = channel_manager
        ch = _make(cm, created)
        register_channel_tools(ch)
        assert _registered("fake_do_special")
        assert _registered("delete_message")

    def test_manager_unregister_cleans_tools(self, channel_manager) -> None:
        cm, created = channel_manager
        _make(cm, created)
        cm.unregister("fake")
        assert not _registered("fake_do_special")
        assert not _registered("delete_message")
