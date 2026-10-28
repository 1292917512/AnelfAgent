"""全局工具复用、用户数据隔离与安装空间门槛。"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app.environment import Environment
from app.manager import Manager
from app.settings import Layout
from app.storage import REQUIREMENTS, inspect_storage


@pytest.mark.parametrize("free", [5 * 1024**3 - 1, 5 * 1024**3, 10 * 1024**3])
def test_free_space_uses_target_volume_and_checks_exact_boundary(tmp_path: Path, free: int,
                                                               monkeypatch: pytest.MonkeyPatch) -> None:
    def usage(path: Path) -> SimpleNamespace:
        assert path == tmp_path
        return SimpleNamespace(free=free)
    monkeypatch.setattr(shutil, "disk_usage", usage)
    target = tmp_path / "new" / "project"
    result = inspect_storage(target)
    assert result["free"] == free
    assert result["sufficient"] is (free >= REQUIREMENTS["project_free_bytes"])
    assert not target.exists()


def test_unreadable_volume_blocks_install_before_stop_or_checkout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manager = Manager(Layout(tmp_path))
    def inaccessible(path: Path) -> None:
        raise PermissionError("cannot read volume")
    monkeypatch.setattr(shutil, "disk_usage", inaccessible)
    monkeypatch.setattr(manager, "_checkout", lambda: pytest.fail("Must not download"))
    monkeypatch.setattr(manager, "_stop", lambda: pytest.fail("Must not stop"))
    assert not manager.status()["storage"]["sufficient"]
    with pytest.raises(RuntimeError, match="Insufficient disk space"):
        manager._install(False)
    assert not (manager.layout.state / "installation.json").exists()


@pytest.mark.parametrize("user_directory", ["", "custom-python"])
def test_uv_finds_user_python_without_downloading_or_using_active_venv(tmp_path: Path, user_directory: str,
                                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    layout = Layout(tmp_path / "project")
    environment = Environment(layout, lambda _: None)
    manager_python = tmp_path / "app-python" / "python.exe"
    manager_python.parent.mkdir(); manager_python.touch()
    found = tmp_path / "user-python.exe"; found.touch()
    monkeypatch.setattr(sys, "_base_executable", str(manager_python))
    monkeypatch.setenv("UV_PYTHON_INSTALL_DIR", str(manager_python.parent))
    monkeypatch.setenv("ANELF_USER_PYTHON_INSTALL_DIR", user_directory)
    monkeypatch.setenv("VIRTUAL_ENV", "unrelated-venv")
    monkeypatch.setattr(environment, "tool", lambda name: "uv")
    calls: list[dict[str, str]] = []
    def run(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        assert args[:3] == ["uv", "python", "find"]
        assert "--no-python-downloads" in args and "--system" in args
        env = kwargs["env"]
        assert "VIRTUAL_ENV" not in env
        calls.append(env)
        return subprocess.CompletedProcess(args, 0 if len(calls) == 2 else 1, str(found), "")
    monkeypatch.setattr(subprocess, "run", run)
    assert environment._existing_python("3.12") == str(found)
    assert calls[0]["UV_PYTHON_INSTALL_DIR"] == str(layout.tools / "python")
    assert calls[1].get("UV_PYTHON_INSTALL_DIR", "") == user_directory
    assert os.environ["UV_PYTHON_INSTALL_DIR"] == str(manager_python.parent)


def test_owned_project_reuses_bootstrap_python(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    layout = Layout(tmp_path)
    (layout.state / "ownership.json").write_text("{}")
    monkeypatch.setenv("UV_PYTHON_INSTALL_DIR", str(Path(sys._base_executable).parent))
    environment = Environment(layout, lambda _: None)
    monkeypatch.setattr(environment, "tool", lambda name: pytest.fail("Do not search or download again"))
    assert environment._existing_python(f"{sys.version_info.major}.{sys.version_info.minor}") == sys._base_executable
