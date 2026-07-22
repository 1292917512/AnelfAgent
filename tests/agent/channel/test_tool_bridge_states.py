"""频道接口按频道开关（channel_tool_states）单元测试。

覆盖：状态持久化读写、注册/重建回读持久化禁用状态、公共能力按频道
schema 过滤与执行守卫、get_channel_tool_info 汇总。
"""

from __future__ import annotations

import json
from typing import Any, Set

import pytest

import agent.channel.manager as mgr
from agent.channel.base import BaseChannel, ChannelConfig
from agent.channel.channel_types import ChannelCapability
from agent.channel.schemas import (
    ChannelInfo, ChannelUser, HealthStatus, SendRequest, SendResponse,
)
from agent.channel.tool_bridge import (
    channel_tool, register_channel_tools, unregister_channel_tools,
    get_channel_tool_info, is_channel_tool_enabled, set_channel_tool_state,
)
from core.config import ConfigManager
from core.entity import EntityRegistry


class _FakeConfig(ChannelConfig):
    pass


class FakeChannel(BaseChannel[_FakeConfig]):
    """测试频道：声明通用能力并标记特有方法。"""

    channel_id = "fake"
    display_name = "Fake"
    capabilities: Set[ChannelCapability] = {
        ChannelCapability.SEND_TEXT,
        ChannelCapability.DELETE_MESSAGE,
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
        return json.dumps({"success": True, "deleted": message_id})

    @channel_tool()
    async def do_special(self, target: str) -> str:
        """特有方法。

        Args:
            target: 目标
        """
        return json.dumps({"success": True, "target": target})


class FakeChannel2(FakeChannel):
    """第二频道：共享 delete_message 公共能力。"""

    channel_id = "fake2"
    capabilities: Set[ChannelCapability] = {
        ChannelCapability.SEND_TEXT,
        ChannelCapability.DELETE_MESSAGE,
    }


@pytest.fixture(autouse=True)
def _clean_states(monkeypatch: pytest.MonkeyPatch):
    """隔离 channel_tool_states / entity_states，并避免测试写真实配置文件。"""
    monkeypatch.setattr(ConfigManager, "save", classmethod(lambda cls: True))
    old_channel_states = ConfigManager.get("channel_tool_states")
    old_entity_states = ConfigManager.get("entity_states")
    ConfigManager.set("channel_tool_states", {})
    ConfigManager.set("entity_states", {})
    yield
    ConfigManager.set("channel_tool_states", old_channel_states or {})
    ConfigManager.set("entity_states", old_entity_states or {})


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


class TestStatePersistence:
    def test_default_enabled(self) -> None:
        assert is_channel_tool_enabled("fake", "delete_message") is True

    def test_set_and_get(self) -> None:
        set_channel_tool_state("fake", "delete_message", False)
        assert is_channel_tool_enabled("fake", "delete_message") is False
        # 其他频道不受影响
        assert is_channel_tool_enabled("fake2", "delete_message") is True

    def test_reenable(self) -> None:
        set_channel_tool_state("fake", "fake_do_special", False)
        set_channel_tool_state("fake", "fake_do_special", True)
        assert is_channel_tool_enabled("fake", "fake_do_special") is True


class TestRegisterReappliesState:
    def test_specific_disable_survives_reregister(self, channel_manager) -> None:
        """特有工具禁用后，频道工具重注册（如频道重启）不得覆盖禁用状态。"""
        cm, created = channel_manager
        ch = _make(cm, created)
        set_channel_tool_state("fake", "fake_do_special", False)
        EntityRegistry.disable("fake_do_special")

        register_channel_tools(ch)

        entity = EntityRegistry.get("fake_do_special")
        assert entity is not None
        assert entity.enabled is False

    def test_global_entity_state_survives_rebuild(self, channel_manager) -> None:
        """公共工具被全局禁用（entity_states）后，重建不得覆盖。"""
        cm, created = channel_manager
        ch = _make(cm, created)
        ConfigManager.set("entity_states", {"delete_message": False})

        register_channel_tools(ch)

        entity = EntityRegistry.get("delete_message")
        assert entity is not None
        assert entity.enabled is False

    def test_per_channel_state_does_not_globally_disable_common(self, channel_manager) -> None:
        """按频道禁用公共能力时，实体保持全局启用（其他频道不受影响）。"""
        cm, created = channel_manager
        _make(cm, created)
        set_channel_tool_state("fake", "delete_message", False)

        entity = EntityRegistry.get("delete_message")
        assert entity is not None
        assert entity.enabled is True


class TestCommonToolGuard:
    @pytest.mark.asyncio
    async def test_disabled_channel_blocked(self, channel_manager) -> None:
        """按频道禁用的公共能力，显式路由到该频道时被守卫拦截。"""
        cm, created = channel_manager
        _make(cm, created)
        _make(cm, created, FakeChannel2)
        set_channel_tool_state("fake", "delete_message", False)

        res = json.loads(await EntityRegistry.execute_tool(
            "delete_message", '{"channel_id":"fake","chat_id":"1","message_id":"2"}'))
        assert res["success"] is False
        assert "禁用" in res["error"]

    @pytest.mark.asyncio
    async def test_other_channel_still_works(self, channel_manager) -> None:
        cm, created = channel_manager
        _make(cm, created)
        _make(cm, created, FakeChannel2)
        set_channel_tool_state("fake", "delete_message", False)

        res = json.loads(await EntityRegistry.execute_tool(
            "delete_message", '{"channel_id":"fake2","chat_id":"1","message_id":"2"}'))
        assert res["success"] is True
        assert res["deleted"] == "2"


class TestPFCFiltering:
    def test_disabled_common_tool_filtered(self, channel_manager) -> None:
        """PFC 频道工具组装过滤被该频道禁用的公共能力。"""
        from agent.mind.prefrontal_cortex import PrefrontalCortex

        cm, created = channel_manager
        _make(cm, created)
        _make(cm, created, FakeChannel2)
        set_channel_tool_state("fake2", "delete_message", False)

        pfc = object.__new__(PrefrontalCortex)
        pfc._channel_manager = cm

        names_fake = {s["function"]["name"] for s in pfc.get_channel_tool_schemas("fake")}
        names_fake2 = {s["function"]["name"] for s in pfc.get_channel_tool_schemas("fake2")}
        assert "delete_message" in names_fake
        assert "delete_message" not in names_fake2


class TestChannelToolInfo:
    def test_info_combines_specific_and_common(self, channel_manager) -> None:
        cm, created = channel_manager
        _make(cm, created)

        tools = {t["name"]: t for t in get_channel_tool_info("fake")}
        assert "fake_do_special" in tools
        assert "delete_message" in tools
        assert tools["fake_do_special"]["common"] is False
        assert tools["delete_message"]["common"] is True
        assert tools["delete_message"]["supporting_channels"] == ["fake"]
        param_names = [p["name"] for p in tools["fake_do_special"]["params"]]
        assert "target" in param_names

    def test_info_reflects_per_channel_state(self, channel_manager) -> None:
        cm, created = channel_manager
        _make(cm, created)
        _make(cm, created, FakeChannel2)
        set_channel_tool_state("fake2", "delete_message", False)

        info_fake = {t["name"]: t for t in get_channel_tool_info("fake")}
        info_fake2 = {t["name"]: t for t in get_channel_tool_info("fake2")}
        assert info_fake["delete_message"]["enabled"] is True
        assert info_fake2["delete_message"]["enabled"] is False
        # 全局实体仍启用
        assert info_fake2["delete_message"]["globally_enabled"] is True

    def test_specific_tool_remains_toggleable_after_disable(self, channel_manager) -> None:
        """专属工具按频道关闭后，globally_enabled 仍为 True（开关可再次打开）。"""
        cm, created = channel_manager
        _make(cm, created)
        # 模拟服务层关闭流程：持久化按频道状态 + 禁用实体
        set_channel_tool_state("fake", "fake_do_special", False)
        EntityRegistry.disable("fake_do_special")

        info = {t["name"]: t for t in get_channel_tool_info("fake")}
        assert info["fake_do_special"]["enabled"] is False
        assert info["fake_do_special"]["globally_enabled"] is True

        # 模拟服务层重新打开
        set_channel_tool_state("fake", "fake_do_special", True)
        EntityRegistry.enable("fake_do_special")
        info = {t["name"]: t for t in get_channel_tool_info("fake")}
        assert info["fake_do_special"]["enabled"] is True

    def test_specific_tool_globally_disabled_via_entity_states(self, channel_manager) -> None:
        """专属工具被能力页全局禁用（entity_states）时，反映为 globally_enabled=False。"""
        cm, created = channel_manager
        _make(cm, created)
        ConfigManager.set("entity_states", {"fake_do_special": False})

        info = {t["name"]: t for t in get_channel_tool_info("fake")}
        assert info["fake_do_special"]["globally_enabled"] is False
        assert info["fake_do_special"]["enabled"] is False
