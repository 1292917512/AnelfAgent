"""Real temporary checkouts exercise move, cancellation and recovery without user data."""

from __future__ import annotations

import errno
import json
import os
import subprocess
from pathlib import Path

import pytest

from app.migration import migrate, recover, validate_project
from core.host_protocol import HostLock


@pytest.fixture
def checkout(tmp_path: Path) -> tuple[Path, Path, Path]:
    source, target, data = tmp_path / "旧项目 with spaces", tmp_path / "app/data/project", tmp_path / "app/data"
    source.mkdir()
    subprocess.run(["git", "init", "-q", str(source)], check=True)
    for name in ("pyproject.toml", "uv.lock", "launch.py"):
        (source / name).write_text("original\n", encoding="utf-8")
    (source / ".gitignore").write_text("logs/\n.venv/\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(source), "add", "."], check=True)
    (source / "launch.py").write_text("uncommitted edit\n", encoding="utf-8")
    (source / "config").mkdir()
    (source / "config/data.db").write_bytes(b"user-data\x00\xff")
    (source / ".venv").mkdir()
    (source / ".venv/custom-package.txt").write_text("preserve packages", encoding="utf-8")
    return source, target, data


def cross_device(monkeypatch: pytest.MonkeyPatch, source: Path, target: Path) -> None:
    rename = Path.rename

    def fail_fast_move(self: Path, destination: Path) -> Path:
        if self == source and destination == target:
            raise OSError(errno.EXDEV, "Different volumes")
        return rename(self, destination)

    monkeypatch.setattr(Path, "rename", fail_fast_move)


@pytest.mark.parametrize("cross", [False, True])
def test_move_preserves_git_data_and_environment_backup(
    checkout: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch, cross: bool,
) -> None:
    source, target, data = checkout
    before = subprocess.check_output(["git", "-C", str(source), "status", "--porcelain"])
    inode = (source / "config/data.db").stat().st_ino
    if cross:
        cross_device(monkeypatch, source, target)
    messages: list[tuple[str, int, int]] = []
    assert migrate(source, target, data, lambda *args: messages.append(args), data / "cancel") == str(target)
    assert not source.exists()
    assert (target / "config/data.db").read_bytes() == b"user-data\x00\xff"
    assert (target / "launch.py").read_text("utf-8") == "uncommitted edit\n"
    assert subprocess.check_output(["git", "-C", str(target), "status", "--porcelain"]) == before
    if not cross:
        assert (target / "config/data.db").stat().st_ino == inode
        assert not any("正在迁移 ·" in row[0] for row in messages)
    else:
        assert any(total > 1 and received > 0 for _text, received, total in messages)
    marker = json.loads((target / "logs/manager/migration.json").read_text("utf-8"))
    assert (target / marker["environment_backup"] / "custom-package.txt").read_text("utf-8") == "preserve packages"
    assert not (target / ".venv").exists()
    assert json.loads((data / "bootstrap.json").read_text("utf-8"))["root"] == str(target)
    assert json.loads((data / "projects.json").read_text("utf-8"))[0]["owned"] is False
    assert not (data / "migration.json").exists()


def test_cancel_cross_volume_and_reject_live_or_locked_checkout(
    checkout: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, target, data = checkout
    cross_device(monkeypatch, source, target)
    cancel = data / "cancel"

    def progress(_message: str, received: int, _total: int) -> None:
        if received:
            cancel.write_text("cancel")

    with pytest.raises(RuntimeError, match="Cancelled"):
        migrate(source, target, data, progress, cancel)
    assert source.exists() and not target.exists()
    assert (source / ".venv/custom-package.txt").is_file()
    assert not list(target.parent.glob(".anelf-transfer-*"))
    cancel.unlink()
    pid = source / "logs/anelf.pid"
    pid.write_text(str(os.getpid()))
    with pytest.raises(RuntimeError, match="仍在运行"):
        migrate(source, target, data, progress, cancel)
    pid.unlink()
    lock = HostLock(source / "logs/anelf.pid.lock")
    assert lock.acquire()
    try:
        with pytest.raises(RuntimeError, match="正在启动"):
            migrate(source, target, data, progress, cancel)
    finally:
        lock.release()
    assert source.exists() and not target.exists()


def test_reject_overlap_occupied_target_and_linked_checkout(checkout: tuple[Path, Path, Path]) -> None:
    source, target, data = checkout
    for destination in (source, source / "nested", source.parent):
        with pytest.raises(ValueError):
            migrate(source, destination, data, lambda *_: None, data / "cancel")
    target.mkdir(parents=True)
    (target / "keep.txt").write_text("keep")
    with pytest.raises(ValueError, match="已有内容"):
        migrate(source, target, data, lambda *_: None, data / "cancel")
    assert (target / "keep.txt").read_text() == "keep"
    (source / ".git").rename(source / "git-data")
    (source / ".git").write_text("gitdir: git-data")
    assert validate_project(source) == source
    with pytest.raises(ValueError, match="worktree"):
        validate_project(source, moving=True)


def test_changed_source_aborts_cross_volume_before_commit(
    checkout: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, target, data = checkout
    cross_device(monkeypatch, source, target)

    def change(message: str, _received: int, _total: int) -> None:
        if message == "正在校验文件完整性":
            (source / "launch.py").write_text("concurrent edit")

    with pytest.raises(RuntimeError, match="变化"):
        migrate(source, target, data, change, data / "cancel")
    assert (source / "launch.py").read_text() == "concurrent edit"
    assert not target.exists()


def test_recover_fast_move_between_rename_and_metadata(checkout: tuple[Path, Path, Path]) -> None:
    source, target, data = checkout
    data.mkdir(parents=True)
    token = "test-interruption"
    info = source.stat()
    (data / "migration.json").write_text(json.dumps({"id": token, "mode": "rename", "source": str(source),
        "target": str(target), "staging": str(target.parent / f".anelf-transfer-{token}"),
        "backup": str(source.parent / f".anelf-original-{token}"), "identity": [info.st_dev, info.st_ino]}))
    source.rename(target)
    assert recover(data) == str(target)
    assert (target / "config/data.db").read_bytes() == b"user-data\x00\xff"
    assert not (data / "migration.json").exists()


@pytest.mark.skipif(os.name == "nt", reason="POSIX symbolic links do not require developer mode")
def test_fast_move_preserves_internal_and_external_link_targets(checkout: tuple[Path, Path, Path]) -> None:
    source, target, data = checkout
    external = source.parent / "external.txt"
    external.write_text("outside")
    (source / "internal").symlink_to(source / "config/data.db")
    (source / "external").symlink_to("../external.txt")
    migrate(source, target, data, lambda *_: None, data / "cancel")
    assert (target / "internal").resolve() == target / "config/data.db"
    assert (target / "external").read_text() == "outside"
