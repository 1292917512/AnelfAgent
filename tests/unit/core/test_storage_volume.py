"""存储卷注册表单元测试 — 登记 / 路径解析优先级 / 指派持久化 / 生效状态。"""

from __future__ import annotations

import json

import pytest

from core.storage_volume import (
    VolumeCapability,
    VolumeDescriptor,
    VolumeKind,
    VolumeRegistry,
    kind_capabilities,
)


def _registry(tmp_path) -> VolumeRegistry:
    return VolumeRegistry(assignment_path=str(tmp_path / "storage_volumes.json"))


@pytest.fixture()
def reg(tmp_path) -> VolumeRegistry:
    registry = _registry(tmp_path)
    registry.register(VolumeDescriptor(
        volume_id="demo",
        name="演示卷",
        description="测试用",
        kind=VolumeKind.SQLITE,
        default_path=lambda: str(tmp_path / "demo.sqlite3"),
    ))
    registry.register(VolumeDescriptor(
        volume_id="pinned",
        name="环境变量钉死卷",
        description="测试用",
        kind=VolumeKind.SQLITE,
        default_path=lambda: str(tmp_path / "pinned.sqlite3"),
        env_override="DEMO_VOLUME_PATH",
    ))
    return registry


class TestRegister:
    def test_get_unknown_raises(self, reg):
        with pytest.raises(KeyError):
            reg.get("nope")

    def test_duplicate_registration_replaces(self, tmp_path, reg):
        reg.register(VolumeDescriptor(
            volume_id="demo",
            name="新描述",
            description="",
            kind=VolumeKind.SQLITE,
            default_path=lambda: "/other.sqlite3",
        ))
        assert reg.get("demo").name == "新描述"
        assert reg.resolve_path("demo") == "/other.sqlite3"

    def test_kind_capabilities(self):
        sqlite_caps = kind_capabilities(VolumeKind.SQLITE)
        assert VolumeCapability.EXPORT_SQL in sqlite_caps
        assert VolumeCapability.RELOCATE in sqlite_caps
        cognee_caps = kind_capabilities(VolumeKind.COGNEE_TREE)
        assert VolumeCapability.EXPORT_SQL not in cognee_caps
        assert VolumeCapability.RELOCATE in cognee_caps
        notes_caps = kind_capabilities(VolumeKind.NOTES_TREE)
        assert notes_caps == frozenset({VolumeCapability.BACKUP, VolumeCapability.RESTORE})


class TestResolvePrecedence:
    def test_default_without_assignment(self, reg, tmp_path):
        assert reg.resolve_path("demo") == str(tmp_path / "demo.sqlite3")
        assert reg.location_source("demo") == "default"

    def test_assignment_overrides_default(self, reg, tmp_path):
        reg.write_location("demo", "/mnt/disk/demo.sqlite3")
        assert reg.resolve_path("demo") == "/mnt/disk/demo.sqlite3"
        assert reg.location_source("demo") == "assignment"

    def test_env_override_beats_assignment(self, reg, monkeypatch, tmp_path):
        reg.write_location("pinned", "/assigned/pinned.sqlite3")
        monkeypatch.setenv("DEMO_VOLUME_PATH", "/from-env/pinned.sqlite3")
        assert reg.resolve_path("pinned") == "/from-env/pinned.sqlite3"
        assert reg.location_source("pinned") == "env"
        monkeypatch.delenv("DEMO_VOLUME_PATH")
        assert reg.resolve_path("pinned") == "/assigned/pinned.sqlite3"

    def test_assignment_persistence_roundtrip(self, tmp_path):
        path = str(tmp_path / "storage_volumes.json")
        first = VolumeRegistry(assignment_path=path)
        first.register(VolumeDescriptor(
            volume_id="demo", name="", description="", kind=VolumeKind.SQLITE,
            default_path=lambda: "default.sqlite3",
        ))
        first.write_location("demo", "/mnt/disk/demo.sqlite3")
        raw = json.loads((tmp_path / "storage_volumes.json").read_text("utf-8"))
        assert raw["volumes"]["demo"]["backend"] == "local"

        second = VolumeRegistry(assignment_path=path)
        second.register(VolumeDescriptor(
            volume_id="demo", name="", description="", kind=VolumeKind.SQLITE,
            default_path=lambda: "default.sqlite3",
        ))
        assert second.resolve_path("demo") == "/mnt/disk/demo.sqlite3"

        second.write_location("demo", "")
        assert second.resolve_path("demo") == "default.sqlite3"

    def test_corrupt_assignment_file_ignored(self, tmp_path):
        (tmp_path / "storage_volumes.json").write_text("{ not json", encoding="utf-8")
        registry = _registry(tmp_path)
        registry.register(VolumeDescriptor(
            volume_id="demo", name="", description="", kind=VolumeKind.SQLITE,
            default_path=lambda: "default.sqlite3",
        ))
        assert registry.resolve_path("demo") == "default.sqlite3"


class TestActiveState:
    def test_needs_restart_after_assignment_change(self, reg):
        reg.mark_active("demo", reg.resolve_path("demo"))
        assert reg.needs_restart("demo") is False
        reg.write_location("demo", "/mnt/disk/demo.sqlite3")
        assert reg.needs_restart("demo") is True
        # 模拟重启后生效：存储重新按解析路径打开
        reg.mark_active("demo", reg.resolve_path("demo"))
        assert reg.needs_restart("demo") is False

    def test_env_pinned_volume_never_needs_restart(self, reg, monkeypatch):
        monkeypatch.setenv("DEMO_VOLUME_PATH", "/from-env/pinned.sqlite3")
        reg.mark_active("pinned", "/stale/path.sqlite3")
        assert reg.needs_restart("pinned") is False

    def test_unmarked_active_reports_false(self, reg):
        reg.write_location("demo", "/mnt/disk/demo.sqlite3")
        assert reg.needs_restart("demo") is False


class TestCustomLocationHooks:
    def test_reader_writer_forwarded(self, tmp_path):
        state = {"path": ""}

        def reader():
            from core.storage_volume import VolumeLocation
            return VolumeLocation(path=state["path"]) if state["path"] else None

        def writer(path):
            state["path"] = path or ""

        registry = _registry(tmp_path)
        registry.register(VolumeDescriptor(
            volume_id="hooked", name="", description="", kind=VolumeKind.COGNEE_TREE,
            default_path=lambda: "/default/cognee",
            location_reader=reader,
            location_writer=writer,
        ))
        assert registry.resolve_path("hooked") == "/default/cognee"
        registry.write_location("hooked", "/data/cognee")
        assert state["path"] == "/data/cognee"
        assert registry.resolve_path("hooked") == "/data/cognee"
        # 自定义钩子不落中央指派文件
        assert not (tmp_path / "storage_volumes.json").exists()
