"""AI 桌面模块框架测试：装饰器注册 / 配置生成 / 注入组装 / 刷新调度。"""

from __future__ import annotations

import pytest

from core.config import ConfigManager
from entities.ai_desktop import framework
from entities.ai_desktop.framework import DesktopModule, desktop_module


@desktop_module
class _DummyModule(DesktopModule):
    """测试用即时型组件。"""

    key = "dummy_test"
    display_name = "测试组件"
    description = "框架单测专用"
    priority = 99
    config_schema = {
        "greeting": {"description": "问候语", "default": "你好"},
    }

    def render(self) -> str:
        return f"[测试] {self.get_config('greeting')}"


class TestRegistration:
    def test_decorator_registers_instance(self) -> None:
        module = framework.get_module("dummy_test")
        assert module is not None
        assert module.display_name == "测试组件"

    def test_missing_key_rejected(self) -> None:
        with pytest.raises(ValueError):
            @desktop_module
            class _NoKey(DesktopModule):
                key = ""

    def test_all_modules_sorted_by_priority(self) -> None:
        priorities = [m.priority for m in framework.all_modules()]
        assert priorities == sorted(priorities)


class TestConfigEntries:
    def test_keys_prefixed(self) -> None:
        entries = framework.config_entries()
        assert entries["ai_desktop_dummy_test_enabled"]["default"] is True
        assert entries["ai_desktop_dummy_test_greeting"]["default"] == "你好"

    def test_get_config_falls_back_to_schema_default(self) -> None:
        module = framework.get_module("dummy_test")
        assert module is not None
        assert module.get_config("greeting") == "你好"
        ConfigManager.set("ai_desktop_dummy_test_greeting", "嗨")
        assert module.get_config("greeting") == "嗨"


class TestRenderContext:
    def test_enabled_module_rendered(self) -> None:
        content = framework.render_context()
        assert content.startswith("[桌面环境]")
        assert "[测试] 你好" in content

    def test_disabled_module_excluded(self) -> None:
        ConfigManager.set("ai_desktop_dummy_test_enabled", False)
        assert "[测试]" not in framework.render_context()

    def test_string_config_bool_coerced(self) -> None:
        ConfigManager.set("ai_desktop_dummy_test_enabled", "false")
        module = framework.get_module("dummy_test")
        assert module is not None
        assert module.is_enabled() is False


class TestRefreshScheduling:
    @pytest.mark.asyncio
    async def test_instant_module_never_refreshed(self) -> None:
        module = framework.get_module("dummy_test")
        assert module is not None
        assert module.due(0.0) is False
        await framework.refresh_due()  # 不抛异常即通过

    @pytest.mark.asyncio
    async def test_refresh_failure_marks_error_and_backs_off(self) -> None:
        @desktop_module
        class _FailModule(DesktopModule):
            key = "fail_test"
            display_name = "失败组件"
            refresh_interval = 60.0

            async def refresh(self) -> None:
                raise RuntimeError("boom")

        module = framework.get_module("fail_test")
        assert module is not None
        await framework.refresh_due(now=1000.0)
        assert module.last_error == "boom"
        assert module.last_refresh == 1000.0
        assert module.due(1030.0) is False
        assert module.due(1061.0) is True
        assert await framework.force_refresh("fail_test") is False
        assert await framework.force_refresh("nonexistent") is False


class TestConfigHotReload:
    def test_config_change_makes_module_due(self) -> None:
        """组件配置变更 → 刷新计时清零 → 下轮调度立即重新采集。"""
        from entities.ai_desktop.framework import handle_config_changed

        module = framework.get_module("dummy_test")
        assert module is not None
        module.refresh_interval = 60.0
        module.last_refresh = 1000.0
        try:
            assert module.due(1010.0) is False
            handle_config_changed("ai_desktop_dummy_test_greeting", "新值")
            assert module.last_refresh == 0.0
            assert module.due(1010.0) is True
        finally:
            module.refresh_interval = 0.0
            module.last_refresh = 0.0

    def test_unrelated_key_ignored(self) -> None:
        from entities.ai_desktop.framework import handle_config_changed

        module = framework.get_module("dummy_test")
        assert module is not None
        module.last_refresh = 1000.0
        handle_config_changed("other_entity_key", "x")
        handle_config_changed("ai_desktop_unknown_x", "x")
        assert module.last_refresh == 1000.0
