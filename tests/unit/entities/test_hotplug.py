"""entities/hotplug.py 实体目录热插拔同步器单元测试。

锁定：sync_entities 的 reconcile 语义（增/删/重载路由、单飞护栏）、
unload_entity 拆除链完整性（注册表/分组/配置组/Lifecycle 组件/sys.modules）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from core.config import ConfigRegistry, register_configs_safe
from core.entity import EntityRegistry
from core.lifecycle import Lifecycle
from entities import hotplug


@pytest.fixture(autouse=True)
def _isolate(monkeypatch: pytest.MonkeyPatch):
    """隔离 hotplug 模块全局态与 Lifecycle（不触碰真实 entities/ 目录与注册表已有实体）。"""
    monkeypatch.setattr(hotplug, "_sync_running", False)
    loaded = set(hotplug._loaded_modules)
    groups = {k: set(v) for k, v in hotplug._entity_groups.items()}
    cfg = {k: set(v) for k, v in hotplug._entity_config_groups.items()}
    lc = {k: set(v) for k, v in hotplug._entity_lifecycles.items()}
    Lifecycle.reset()
    yield
    for name in ("_hp_fake", "_hp_shared"):
        for tool in hotplug._entity_tool_names(name):
            EntityRegistry.unregister(tool)
    for group in ("_hp_g1", "_hp_g2"):
        EntityRegistry.unregister_group(group)
    ConfigRegistry.unregister_group("entity/_hp_g1")
    hotplug._loaded_modules.clear()
    hotplug._loaded_modules.update(loaded)
    hotplug._entity_groups.clear()
    hotplug._entity_groups.update(groups)
    hotplug._entity_config_groups.clear()
    hotplug._entity_config_groups.update(cfg)
    hotplug._entity_lifecycles.clear()
    hotplug._entity_lifecycles.update(lc)
    Lifecycle.reset()


def _register_fake_tool(name: str, entity: str, group: str) -> None:
    """注册一个 func.__module__ 伪造为 entities.<entity>.tools 的工具。"""
    def _func() -> str:
        return "ok"
    _func.__module__ = f"entities.{entity}.tools"
    EntityRegistry.register_tool(name=name, func=_func, group=group)


class TestScanEntityDirs:
    def test_only_dirs_with_tools_py(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        (tmp_path / "alpha").mkdir()
        (tmp_path / "alpha" / "tools.py").touch()
        (tmp_path / "beta").mkdir()  # 无 tools.py
        (tmp_path / "_private").mkdir()
        (tmp_path / "_private" / "tools.py").touch()
        (tmp_path / "loose.py").touch()  # 散文件不算
        monkeypatch.setattr(hotplug, "_entities_dir", lambda: tmp_path)

        assert hotplug.scan_entity_dirs() == {"alpha"}


class TestSyncEntities:
    async def test_add_then_remove(self, monkeypatch: pytest.MonkeyPatch) -> None:
        dirs = {"e1"}
        monkeypatch.setattr(hotplug, "scan_entity_dirs", lambda: set(dirs))
        monkeypatch.setattr(hotplug, "load_entity", lambda name: True)

        lifecycles: list[str] = []

        async def _fake_lifecycle(name: str) -> bool:
            lifecycles.append(name)
            return True

        monkeypatch.setattr(hotplug, "register_entity_lifecycle", _fake_lifecycle)

        result = await hotplug.sync_entities()
        assert result["added"] == ["e1"] and result["removed"] == []
        assert "e1" in hotplug._loaded_modules
        assert lifecycles == ["e1"]

        # 目录消失 → 热拔除
        dirs.clear()
        result = await hotplug.sync_entities()
        assert result["removed"] == ["e1"]
        assert "e1" not in hotplug._loaded_modules

    async def test_reload_existing_only_when_requested(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(hotplug, "scan_entity_dirs", lambda: {"e1"})
        hotplug._loaded_modules.add("e1")
        reloaded: list[str] = []
        monkeypatch.setattr(
            hotplug, "reload_entity",
            lambda name: reloaded.append(name) is None or True,
        )

        result = await hotplug.sync_entities(reload_existing=False)
        assert result["reloaded"] == [] and reloaded == []

        result = await hotplug.sync_entities(reload_existing=True)
        assert result["reloaded"] == ["e1"] and reloaded == ["e1"]

    async def test_singleflight_skip(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(hotplug, "_sync_running", True)
        result = await hotplug.sync_entities()
        assert result["skipped"] == "in_progress"

    async def test_load_failure_not_marked_loaded(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(hotplug, "scan_entity_dirs", lambda: {"bad"})
        monkeypatch.setattr(hotplug, "load_entity", lambda name: False)

        result = await hotplug.sync_entities()
        assert result["failed"] == ["bad"]
        assert "bad" not in hotplug._loaded_modules


class TestUnloadEntity:
    async def test_full_teardown(self) -> None:
        """拆除链：工具注销 → 独占分组清理（含 manifest/权重）→ 配置组 → Lifecycle 组件 → 已加载集合。"""
        _register_fake_tool("_hp_t1", "_hp_fake", "_hp_g1")
        EntityRegistry.register_group("_hp_g1", "测试分组")
        EntityRegistry.register_group_manifest("_hp_g1", {"order": 42})
        EntityRegistry.register_group_order("_hp_g1", 42)
        register_configs_safe({
            "entity/_hp_g1": {"_hp_cfg_key": {"description": "t", "default": 1}},
        })
        cleaned: list[str] = []
        Lifecycle.register("_hp_comp", None, cleanup=lambda: cleaned.append("_hp_comp"))

        hotplug._entity_groups["_hp_fake"] = {"_hp_g1"}
        hotplug._entity_config_groups["_hp_fake"] = {"entity/_hp_g1"}
        hotplug._entity_lifecycles["_hp_fake"] = {"_hp_comp"}
        hotplug._loaded_modules.add("_hp_fake")

        await hotplug.unload_entity("_hp_fake")

        assert EntityRegistry.get("_hp_t1") is None
        assert "_hp_g1" not in EntityRegistry.list_groups()
        assert EntityRegistry.get_group_manifest("_hp_g1") == {}
        # 权重回落到未注册兜底（分组不在默认表 → 删除）
        assert EntityRegistry.group_sort_key("_hp_g1")[0] == 1000
        assert "entity/_hp_g1" not in ConfigRegistry.get_all_groups()
        assert cleaned == ["_hp_comp"]
        assert Lifecycle.get("_hp_comp") is None
        assert "_hp_fake" not in hotplug._loaded_modules

    async def test_shared_group_is_kept(self) -> None:
        """分组内仍有其他来源工具时不回收分组（共享分组保护）。"""
        _register_fake_tool("_hp_t1", "_hp_fake", "_hp_g2")

        def _other() -> str:
            return "ok"
        EntityRegistry.register_tool(name="_hp_t2", func=_other, group="_hp_g2")
        hotplug._entity_groups["_hp_fake"] = {"_hp_g2"}
        hotplug._loaded_modules.add("_hp_fake")
        try:
            await hotplug.unload_entity("_hp_fake")
            assert EntityRegistry.get("_hp_t1") is None
            assert EntityRegistry.get("_hp_t2") is not None
            assert "_hp_g2" in EntityRegistry.list_groups()
        finally:
            EntityRegistry.unregister("_hp_t2")
            EntityRegistry.unregister_group("_hp_g2")
