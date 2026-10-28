"""Real temporary checkouts exercise move, cancellation and recovery without user data."""

from __future__ import annotations

import errno
import json
import os
import subprocess
import tempfile
from contextlib import ExitStack
from pathlib import Path, PureWindowsPath

import pytest

from app.migration import migrate, recover, validate_git_link, validate_project
from app.migration_links import Link, relocated_target
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
@pytest.mark.parametrize("cross", [False, True])
def test_move_preserves_internal_and_external_link_targets(
    checkout: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch, cross: bool,
) -> None:
    source, target, data = checkout
    external = source.parent / "external.txt"
    external.write_text("outside")
    (source / "internal").symlink_to(source / "config/data.db")
    (source / "external").symlink_to("../external.txt")
    (source / ".venv/package-link").symlink_to(source / ".venv/custom-package.txt")
    if cross:
        cross_device(monkeypatch, source, target)
    migrate(source, target, data, lambda *_: None, data / "cancel")
    assert (target / "internal").resolve() == target / "config/data.db"
    assert (target / "external").read_text() == "outside"
    saved = json.loads((target / "logs/manager/migration.json").read_text())["environment_backup"]
    assert (target / saved / "package-link").read_text() == "preserve packages"


@pytest.mark.parametrize("cross", [False, True])
def test_nested_relative_submodule_moves_with_the_checkout(
    checkout: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch, cross: bool,
) -> None:
    source, target, data = checkout
    upstream = source.parent / "submodule-upstream"
    upstream.mkdir()
    subprocess.run(["git", "init", "-q", str(upstream)], check=True)
    (upstream / "module.txt").write_text("committed\n")
    subprocess.run(["git", "-C", str(upstream), "add", "."], check=True)
    subprocess.run(["git", "-C", str(upstream), "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
                    "commit", "-qm", "fixture"], check=True)
    reference = source / ".other/Reference"
    reference.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(reference)], check=True)
    subprocess.run(["git", "-c", "protocol.file.allow=always", "-C", str(reference), "submodule", "add",
                    str(upstream), "vendor/module"], check=True, capture_output=True)
    module = reference / "vendor/module"
    (module / "module.txt").write_text("local uncommitted change\n")
    before = subprocess.check_output(["git", "-C", str(module), "status", "--porcelain"])
    if cross:
        cross_device(monkeypatch, source, target)
    migrate(source, target, data, lambda *_: None, data / "cancel")
    moved = target / ".other/Reference/vendor/module"
    assert subprocess.check_output(["git", "-C", str(moved), "status", "--porcelain"]) == before
    assert (moved / "module.txt").read_text() == "local uncommitted change\n"
    assert "gitdir: ../../.git/modules/vendor/module" in (moved / ".git").read_text()


def test_external_git_link_is_rejected_with_its_relative_path(checkout: tuple[Path, Path, Path]) -> None:
    source, _target, _data = checkout
    linked = source / "reference"
    linked.mkdir()
    (linked / ".git").write_text("gitdir: ../../external.git\n")
    with pytest.raises(ValueError, match=r"reference[/\\]\.git"):
        validate_git_link(linked / ".git", source)


@pytest.mark.skipif(os.name != "nt", reason="Windows directory junctions")
@pytest.mark.parametrize("mode", ["rename", "copy", "volume", "recover"])
def test_windows_junctions_preserve_internal_packages_and_external_data(
    checkout: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch, mode: str,
) -> None:
    import _winapi

    source, target, data = checkout
    package = source / "node_modules/.pnpm/example/node_modules/example"
    package.mkdir(parents=True)
    (package / "index.js").write_text("local package")
    external = source.parent / "external-data"
    external.mkdir()
    (external / "keep.txt").write_text("external data")
    _winapi.CreateJunction(str(package), str(source / "node_modules/example"))
    _winapi.CreateJunction(str(external), str(source / "external"))
    extra = source / ".venv/extra"
    extra.mkdir()
    (extra / "keep.txt").write_text("extra package")
    _winapi.CreateJunction(str(extra), str(source / ".venv/alias"))
    _winapi.CreateJunction(str(source / ".venv"), str(source / "old-environment"))
    with ExitStack() as stack:
        if mode == "volume":
            candidates = [Path.home() / "AppData/Local/Temp", Path.cwd() / "app/build"]
            other = next((item for item in candidates if item.exists() and item.stat().st_dev != source.stat().st_dev), None)
            if other is None:
                pytest.skip("A second writable volume is unavailable")
            base = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix="anelf-migration-test-", dir=other)))
            data = base / "data"
            target = data / "project"
        elif mode in {"copy", "recover"}:
            cross_device(monkeypatch, source, target)
        if mode == "recover":
            rename = Path.rename
            with monkeypatch.context() as patch:
                def interrupt(self: Path, destination: Path) -> Path:
                    if ".anelf-link-" in self.name:
                        raise OSError("Interrupted while replacing a junction")
                    return rename(self, destination)
                patch.setattr(Path, "rename", interrupt)
                with pytest.raises(OSError, match="Interrupted"):
                    migrate(source, target, data, lambda *_: None, data / "cancel")
            assert (data / "migration.json").exists()
            assert recover(data) == str(target)
        else:
            migrate(source, target, data, lambda *_: None, data / "cancel")
        link = target / "node_modules/example"
        assert link.is_junction() and link.samefile(target / package.relative_to(source))
        assert (link / "index.js").read_text() == "local package"
        assert (target / "external").is_junction() and (target / "external").samefile(external)
        saved = json.loads((target / "logs/manager/migration.json").read_text())["environment_backup"]
        assert (target / saved / "alias/keep.txt").read_text() == "extra package"
        assert (target / "old-environment").samefile(target / saved)
        assert not source.exists()
    assert (external / "keep.txt").read_text() == "external data"


def test_windows_link_paths_normalize_extended_drives_and_unc_shares() -> None:
    source, target = PureWindowsPath("E:/project"), PureWindowsPath("D:/app/project")
    internal = PureWindowsPath(r"\\?\E:\project\packages\example")
    assert relocated_target(source, target, "node_modules/example", internal, junction=True) == r"D:\app\project\packages\example"
    assert relocated_target(source, target, "node_modules/example", internal, junction=False) == r"..\packages\example"
    external = PureWindowsPath(r"\\?\E:\shared\example")
    assert relocated_target(source, target, "link", external, junction=False) == r"E:\shared\example"
    network = PureWindowsPath(r"\\?\UNC\host\share\project\package")
    assert relocated_target(PureWindowsPath(r"\\host\share\project"), target, "link", network, junction=True) == r"D:\app\project\package"


@pytest.mark.skipif(os.name != "nt", reason="Windows directory junctions")
def test_link_creation_failure_precedes_file_copy_and_preserves_external_target(
    checkout: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch,
) -> None:
    import _winapi

    import app.migration as migration

    source, target, data = checkout
    _winapi.CreateJunction(str(source / "config"), str(source / "config-link"))
    cross_device(monkeypatch, source, target)
    create = migration.create_link

    def fail_staging(item: Path, link: Link, *, original: bool = False) -> None:
        if any(part.startswith(".anelf-transfer-") for part in item.parts):
            raise RuntimeError("Destination does not support links")
        create(item, link, original=original)

    monkeypatch.setattr(migration, "create_link", fail_staging)
    messages: list[str] = []
    with pytest.raises(RuntimeError, match="does not support"):
        migrate(source, target, data, lambda message, *_: messages.append(message), data / "cancel")
    assert not any(message.startswith("正在迁移 ·") for message in messages)
    assert (source / "config/data.db").read_bytes() == b"user-data\x00\xff"
    assert (source / "config-link").is_junction() and not target.exists()
    assert not list(target.parent.glob(".anelf-transfer-*"))
