"""心跳服务配置路由单元测试。

回归背景（2026-09-17 心跳间隔双源事故）：heartbeat.json 的 interval_seconds
曾是死配置——Web 心跳页写它，实际循环读 mind.heartbeat_interval，改值永不
生效且两处展示分裂。收口后：读侧返回真源派生视图，写侧路由 save_mind_config
（与配置中心同一 coerce + clamp 纪律）。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from core.config import ConfigItem
from services.heartbeat import HeartbeatService, HeartbeatServiceError


@pytest.fixture
def mind_item(monkeypatch: pytest.MonkeyPatch) -> ConfigItem:
    """打桩 heartbeat_interval 注册项（float 类型、下限 60，与 mind/core 注册一致）。"""
    item = ConfigItem(
        key="heartbeat_interval", group="mind/core",
        description="心跳间隔", default_value=300.0, min_value=60,
    )
    monkeypatch.setattr(
        "core.config.ConfigRegistry.get_item", classmethod(lambda cls, key: item),
    )
    return item


def _stub_provider(monkeypatch: pytest.MonkeyPatch, saved: dict) -> None:
    provider = SimpleNamespace(save_mind_config=lambda **kw: saved.update(kw))
    monkeypatch.setattr("agent.config.get_config_provider", lambda: provider)


class TestIntervalWriteRouting:
    def test_routes_to_mind_config(self, monkeypatch, mind_item) -> None:
        saved: dict = {}
        _stub_provider(monkeypatch, saved)
        HeartbeatService._save_interval_seconds(30000)
        assert saved == {"heartbeat_interval": 30000.0}
        HeartbeatService._save_interval_seconds("600")
        assert saved["heartbeat_interval"] == 600.0

    def test_clamps_to_registered_floor(self, monkeypatch, mind_item) -> None:
        saved: dict = {}
        _stub_provider(monkeypatch, saved)
        HeartbeatService._save_interval_seconds(10)
        assert saved["heartbeat_interval"] == 60.0

    def test_rejects_invalid_value(self, monkeypatch, mind_item) -> None:
        _stub_provider(monkeypatch, {})
        with pytest.raises(HeartbeatServiceError):
            HeartbeatService._save_interval_seconds("abc")

    def test_save_config_interval_only_leaves_no_dead_field(self, monkeypatch, mind_item) -> None:
        """只改间隔时：值进 mind 真源，heartbeat.json 不再持有间隔字段。"""
        from agent.heartbeat.config import HeartbeatConfig

        cfg = HeartbeatConfig()
        monkeypatch.setattr(HeartbeatConfig, "save", lambda self, path=None: None)
        monkeypatch.setattr("agent.heartbeat.config.get_heartbeat_config", lambda: cfg)
        monkeypatch.setattr("services.heartbeat.get_runtime", lambda: None)
        saved: dict = {}
        _stub_provider(monkeypatch, saved)

        HeartbeatService.save_config({"interval_seconds": 30000})

        assert saved == {"heartbeat_interval": 30000.0}
        assert "interval_seconds" not in cfg.to_dict()


class TestIntervalReadView:
    def test_get_config_interval_is_derived_from_mind(self, monkeypatch) -> None:
        from agent.heartbeat.config import HeartbeatConfig

        monkeypatch.setattr("agent.heartbeat.config.get_heartbeat_config", lambda: HeartbeatConfig())
        monkeypatch.setattr("agent.heartbeat.config.current_interval_seconds", lambda: 6000)

        cfg = HeartbeatService.get_config()

        assert cfg["interval_seconds"] == 6000
        assert "task_schedules" in cfg and "enabled" in cfg
