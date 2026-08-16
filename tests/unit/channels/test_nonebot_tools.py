"""NoneBot AI 工具注册与校验测试（注册后清理，不污染全局注册表）。"""

from __future__ import annotations

import json

import pytest

from channels.nonebot_bridge import tools as nb_tools
from core.entity import EntityRegistry

TOOL_NAMES = [spec[0] for spec in nb_tools._TOOL_SPECS]


@pytest.fixture()
def clean_tool_registry():
    """注册前复位标记，用例后注销全部工具。"""
    nb_tools._TOOLS_REGISTERED = False  # noqa: SLF001 - 测试需复位模块态
    yield
    for name in TOOL_NAMES:
        EntityRegistry.unregister(name)
    nb_tools._TOOLS_REGISTERED = False  # noqa: SLF001


class TestToolRegistration:
    """注册幂等性与敏感门控。"""

    def test_register_all_tools(self, clean_tool_registry) -> None:
        count = nb_tools.register_nonebot_tools()
        assert count == len(TOOL_NAMES)
        assert count >= 13
        for name in TOOL_NAMES:
            entity = EntityRegistry.get(name)
            assert entity is not None, f"工具未注册: {name}"
            assert entity.description

    def test_register_idempotent(self, clean_tool_registry) -> None:
        assert nb_tools.register_nonebot_tools() == len(TOOL_NAMES)
        assert nb_tools.register_nonebot_tools() == 0
        assert EntityRegistry.get("nonebot_status") is not None

    def test_sensitive_tools_gated(self, clean_tool_registry) -> None:
        nb_tools.register_nonebot_tools()
        for name in ("nonebot_manage_adapter", "nonebot_manage_plugin",
                     "nonebot_env_manage", "nonebot_config_set", "nonebot_lifecycle"):
            entity = EntityRegistry.get(name)
            assert entity is not None and entity.check_fn is not None, name

    def test_query_tools_not_gated(self, clean_tool_registry) -> None:
        nb_tools.register_nonebot_tools()
        for name in ("nonebot_status", "nonebot_env_status", "nonebot_store_search"):
            entity = EntityRegistry.get(name)
            assert entity is not None and entity.check_fn is None, name


class TestToolHandlerValidation:
    """处理器参数校验（不触服务层副作用）。"""

    @pytest.mark.asyncio
    async def test_adapter_unknown_action(self) -> None:
        result = await nb_tools._tool_manage_adapter("explode", "onebot_v11")
        assert json.loads(result)["success"] is False

    @pytest.mark.asyncio
    async def test_plugin_unknown_action(self) -> None:
        result = await nb_tools._tool_manage_plugin("explode", "m")
        assert json.loads(result)["success"] is False

    @pytest.mark.asyncio
    async def test_env_unknown_action(self) -> None:
        result = await nb_tools._tool_env_manage("explode", None)
        assert json.loads(result)["success"] is False

    @pytest.mark.asyncio
    async def test_lifecycle_unknown_action(self) -> None:
        result = await nb_tools._tool_lifecycle("explode")
        assert json.loads(result)["success"] is False

    @pytest.mark.asyncio
    async def test_config_set_unknown_key(self) -> None:
        result = await nb_tools._tool_config_set("bogus_key", "1")
        assert json.loads(result)["success"] is False


class TestSendMediaParams:
    """nonebot_send 媒体参数校验与路由。"""

    @pytest.mark.asyncio
    async def test_no_payload_rejected(self) -> None:
        result = await nb_tools._tool_send("12345")
        assert json.loads(result)["success"] is False

    @pytest.mark.asyncio
    async def test_multiple_media_rejected(self) -> None:
        result = await nb_tools._tool_send("12345", image="/a.png", voice="/b.silk")
        assert json.loads(result)["success"] is False

    @pytest.mark.asyncio
    async def test_voice_routes_to_media(self, monkeypatch) -> None:
        calls = []

        class _FakeSvc:
            async def send_media_to_platform(self, chat_id, kind, source, **kw):
                calls.append((chat_id, kind, source))
                return {"success": True}

        import services.nonebot as svc_mod

        monkeypatch.setattr(svc_mod, "NoneBotService", _FakeSvc)
        result = await nb_tools._tool_send("70001", voice="/x/a.silk", channel_type="group")
        assert json.loads(result)["success"] is True
        assert calls == [("70001", "voice", "/x/a.silk")]
