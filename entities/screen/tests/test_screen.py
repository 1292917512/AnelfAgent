"""屏幕源实体测试：组件注册与配置面。"""

from __future__ import annotations


class TestScreenSource:
    def test_source_registered_on_import(self) -> None:
        """实体包导入即把屏幕源注册进核心视觉源注册表。"""
        import entities.screen  # noqa: F401
        from agent.vision.framework import all_sources, pollable_sources
        keys = {s.key for s in all_sources()}
        assert "screen" in keys
        assert "screen" in {s.key for s in pollable_sources()}

    def test_source_metadata(self) -> None:
        from entities.screen.source import ScreenSource
        src = ScreenSource()
        assert src.key == "screen"
        assert src.poll_interval > 0
        assert src.can_capture is True

    def test_monitor_config_registered(self) -> None:
        import entities.screen  # noqa: F401
        from core.config import ConfigRegistry
        item = ConfigRegistry.get_item("screen_monitor")
        assert item is not None
