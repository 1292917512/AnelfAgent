"""EntityRegistry 启停状态与 entity_states 持久化（set_enabled / set_group_enabled）单元测试。

锁定：启停持久化的唯一实现——用户显式开关（Web/AI 同路径）写 entity_states，
回放/派生状态路径（persist=False）仅改内存态；group_has_enabled_tools
与实体目录的分组可见性同口径（任一启用即可见）。
"""

from __future__ import annotations

import pytest

from core.config import ConfigManager
from core.entity import EntityRegistry


@pytest.fixture(autouse=True)
def _clean(monkeypatch: pytest.MonkeyPatch):
    """隔离 entity_states 并避免测试写真实配置文件。"""
    monkeypatch.setattr(ConfigManager, "save", classmethod(lambda cls: True))
    old = ConfigManager.get("entity_states")
    ConfigManager.set("entity_states", {})
    yield
    ConfigManager.set("entity_states", old or {})


def _states() -> dict:
    states = ConfigManager.get("entity_states", {})
    return states if isinstance(states, dict) else {}


def _register(name: str, group: str = "st_group") -> None:
    EntityRegistry.register_tool(name=name, func=lambda: "ok", group=group)


class TestSetEnabled:
    def test_persist_default_writes_entity_states(self) -> None:
        _register("st_t1")
        try:
            assert EntityRegistry.set_enabled("st_t1", False) is True
            assert _states() == {"st_t1": False}
            assert EntityRegistry.set_enabled("st_t1", True) is True
            assert _states() == {"st_t1": True}
        finally:
            EntityRegistry.unregister("st_t1")

    def test_persist_false_keeps_memory_only(self) -> None:
        """回放/派生状态路径：仅改内存态，不动持久化记录。"""
        _register("st_t2")
        try:
            assert EntityRegistry.set_enabled("st_t2", False, persist=False) is True
            assert _states() == {}
            assert EntityRegistry.get("st_t2").enabled is False  # type: ignore[union-attr]
        finally:
            EntityRegistry.unregister("st_t2")

    def test_unknown_entity_returns_false_and_not_persisted(self) -> None:
        assert EntityRegistry.set_enabled("st_ghost", False) is False
        assert _states() == {}


class TestSetGroupEnabled:
    def test_persists_all_tools_in_group(self) -> None:
        _register("st_g1")
        _register("st_g2")
        _register("st_other", group="st_other_group")
        try:
            assert EntityRegistry.set_group_enabled("st_group", False) == 2
            assert _states() == {"st_g1": False, "st_g2": False}
            assert EntityRegistry.get("st_other").enabled is True  # type: ignore[union-attr]

            assert EntityRegistry.set_group_enabled("st_group", True) == 2
            assert _states() == {"st_g1": True, "st_g2": True}
        finally:
            EntityRegistry.unregister("st_g1")
            EntityRegistry.unregister("st_g2")
            EntityRegistry.unregister("st_other")

    def test_count_reflects_changed_only(self) -> None:
        """受影响数只计实际翻转的实体（幂等语义）。"""
        _register("st_g1")
        _register("st_g2")
        try:
            EntityRegistry.set_enabled("st_g1", False, persist=False)
            assert EntityRegistry.set_group_enabled("st_group", False) == 1
        finally:
            EntityRegistry.unregister("st_g1")
            EntityRegistry.unregister("st_g2")


class TestGroupHasEnabledTools:
    def test_visibility_parity_with_catalog(self) -> None:
        """任一启用即 True（与实体目录分组可见性同口径）；全禁用/空组为 False。"""
        _register("st_g1")
        _register("st_g2")
        try:
            assert EntityRegistry.group_has_enabled_tools("st_group") is True
            assert EntityRegistry.group_has_enabled_tools("st_missing") is False

            EntityRegistry.disable("st_g1")
            assert EntityRegistry.group_has_enabled_tools("st_group") is True

            EntityRegistry.disable("st_g2")
            assert EntityRegistry.group_has_enabled_tools("st_group") is False
        finally:
            EntityRegistry.unregister("st_g1")
            EntityRegistry.unregister("st_g2")
