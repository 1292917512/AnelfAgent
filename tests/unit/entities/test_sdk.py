"""entities/_sdk 桥接层单元测试：工具参数提取 + push_notify 推送。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from entities import _sdk
from entities._sdk import _extract_params

# ------------------------------------------------------------------
# _extract_params（复现历史 bug：**kwargs 容错参数曾生成虚假 schema 字段）
# ------------------------------------------------------------------

def _tool_with_kwargs(image_path: str = "", prompt: str = "", **kwargs: str) -> str:
    """示例工具。

    Args:
        image_path: 图片路径
        prompt: 提示词
    """
    return ""


def _tool_with_args(first: str, *args: str, flag: bool = False) -> str:
    """示例工具。

    Args:
        first: 首个参数
        flag: 开关
    """
    return ""


class TestExtractParams:
    def test_skips_var_keyword(self) -> None:
        params = _extract_params(_tool_with_kwargs)
        names = [p.name for p in params]
        assert names == ["image_path", "prompt"]
        assert all(not p.required for p in params)

    def test_skips_var_positional(self) -> None:
        params = _extract_params(_tool_with_args)
        names = [p.name for p in params]
        assert names == ["first", "flag"]
        assert params[0].required is True
        assert params[1].required is False


# ------------------------------------------------------------------
# push_notify 桥接
# ------------------------------------------------------------------

class _FakeHub:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def push(self, scope, source, content, channel="", trigger=True):
        self.calls.append((scope, source, content, channel, trigger))
        return True


@pytest.fixture
def hub(monkeypatch: pytest.MonkeyPatch) -> _FakeHub:
    fake = _FakeHub()
    runtime = SimpleNamespace(mind=SimpleNamespace(push_hub=fake))
    from agent.runtime import singleton
    monkeypatch.setattr(singleton, "require_runtime", lambda: runtime)
    return fake


class TestPushNotify:
    def test_delegates_to_hub(self, hub: _FakeHub) -> None:
        ok = _sdk.push_notify("声纹库更新完成", "voiceprint", scope="user_webui:u1", channel="webui")
        assert ok is True
        assert hub.calls == [("user_webui:u1", "voiceprint", "声纹库更新完成", "webui", True)]

    def test_auto_scope_from_context(self, hub: _FakeHub, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(_sdk, "get_current_scope", lambda: "user_qq:42")
        ok = _sdk.push_notify("内容", "devops", trigger=False)
        assert ok is True
        assert hub.calls[0][0] == "user_qq:42"
        assert hub.calls[0][4] is False

    def test_global_scope_becomes_empty(self, hub: _FakeHub, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(_sdk, "get_current_scope", lambda: "_global")
        assert _sdk.push_notify("内容", "watcher") is True
        assert hub.calls[0][0] == ""

    def test_empty_content_rejected(self, hub: _FakeHub) -> None:
        assert _sdk.push_notify("  ", "voiceprint", scope="user_webui:u1") is False
        assert hub.calls == []

    def test_system_down_returns_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from agent.runtime import singleton

        def _raise():
            raise RuntimeError("AgentRuntime 尚未初始化")

        monkeypatch.setattr(singleton, "get_runtime", _raise)
        assert _sdk.push_notify("内容", "voiceprint", scope="user_webui:u1") is False


# ------------------------------------------------------------------
# context_provider 注入开关（inject_key）兜底注册
# ------------------------------------------------------------------

class TestContextProviderInjectKey:
    def test_auto_registers_config_item(self) -> None:
        """inject_key + group 声明时，未注册的配置键自动兜底注册进 entity/<group> 组。"""
        from core.config import ConfigRegistry
        from core.context_provider import ContextProviderRegistry
        from entities._sdk import context_provider

        @context_provider(
            name="sdk_demo_provider", group="sdk_demo",
            inject_key="sdk_demo_context_inject",
        )
        async def _provide(scope: str) -> None:
            return None

        item = ConfigRegistry.get_item("sdk_demo_context_inject")
        assert item is not None
        assert item.group == "entity/sdk_demo"
        assert item.default_value is True
        # 注册表为全局共享，按名字定位（不能假定注册顺序）
        meta = next(
            m for m in ContextProviderRegistry.get_all()
            if m.name == "sdk_demo_provider"
        )
        assert meta.inject_key == "sdk_demo_context_inject"

    def test_existing_definition_not_overwritten(self) -> None:
        """实体自行 register_configs 声明的定义优先，兜底注册不覆盖。"""
        from core.config import ConfigRegistry, register_configs_safe
        from entities._sdk import context_provider

        register_configs_safe({
            "entity/sdk_demo2": {
                "sdk_demo2_context_inject": {
                    "description": "自定义注入开关描述",
                    "default": False,
                },
            },
        })

        @context_provider(
            name="sdk_demo2_provider", group="sdk_demo2",
            inject_key="sdk_demo2_context_inject",
        )
        async def _provide(scope: str) -> None:
            return None

        item = ConfigRegistry.get_item("sdk_demo2_context_inject")
        assert item is not None
        assert item.description == "自定义注入开关描述"
        assert item.default_value is False


# ------------------------------------------------------------------
# entity_manifest order → 分组排序权重接线（实体自决排序）
# ------------------------------------------------------------------

class TestEntityManifestOrder:
    def test_order_wires_to_group_order_weight(self) -> None:
        """entity_manifest 的 order 同步注册为分组排序权重。"""
        from core.entity import EntityRegistry
        from entities._sdk import entity_manifest

        entity_manifest(display_name="t", group="_sdk_order_demo", order=42)
        try:
            assert EntityRegistry.group_sort_key("_sdk_order_demo") == (42, "_sdk_order_demo")
            assert EntityRegistry.get_group_manifest("_sdk_order_demo")["order"] == 42
        finally:
            EntityRegistry.unregister_group("_sdk_order_demo")
        # 注销后权重与 manifest 一并回收（不在默认表的分组）
        assert EntityRegistry.group_sort_key("_sdk_order_demo")[0] == 1000
        assert EntityRegistry.get_group_manifest("_sdk_order_demo") == {}

    def test_unregistered_group_sorts_last(self) -> None:
        """未注册权重的分组按字母序排在已注册分组之后。"""
        from core.entity import EntityRegistry

        assert EntityRegistry.group_sort_key("memory") < EntityRegistry.group_sort_key("_zzz_never")
        key = EntityRegistry.group_sort_key("_zzz_never")
        assert key == (1000, "_zzz_never")

    def test_default_table_bands_keep_similar_groups_adjacent(self) -> None:
        """默认权重表分段：同类分组同段相邻（记忆/规划/能力/运维/管理/界面）。"""
        from core.entity import _DEFAULT_GROUP_ORDER

        bands = [
            {"output", "thinking"},
            {"memory", "graph", "notes"},
            {"planning", "skills", "delegation"},
            {"web", "media", "os", "environment", "ssh", "sticker", "share", "voiceprint", "vault"},
            {"model_control", "ollama", "logs", "devops"},
            {"channel_ops", "entity", "mcp_manage", "plugins"},
            {"ui", "session"},
        ]
        weights = [{_DEFAULT_GROUP_ORDER[g] for g in band} for band in bands]
        for lower, upper in zip(weights, weights[1:], strict=False):
            assert max(lower) < min(upper)
