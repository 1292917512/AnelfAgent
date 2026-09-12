"""工作区路径统一锚定回归测试（2026-09-12 AI 误写项目根事故）。

事故链：python_exec 仅在沙箱开启时设定子进程 cwd，关闭时以项目根为
cwd，AI 代码中的相对路径（如 projects/mmd-v2）误落到项目根；且
workspace_root 相对配置经 os.path.abspath 依赖进程启动目录。

统一后语义：所有文件/Shell/Python 入口的相对路径一律锚定
paths.get_workspace_root()（相对配置以项目根为基准 + 防项目根守卫）。
"""

from __future__ import annotations

import os
from typing import Any, Dict

import pytest

from entities.filesystem import paths as paths_mod


def _set_workspace_cfg(monkeypatch: pytest.MonkeyPatch, cfg: str) -> None:
    monkeypatch.setattr(paths_mod, "get_workspace_config", lambda: cfg)


class TestGetWorkspaceRoot:
    def test_default_relative_anchors_project_root(
            self, monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
        """默认相对配置：以项目根为基准解析（与进程 cwd 无关）。"""
        from core import path as path_mod
        monkeypatch.setattr(path_mod, "_PROJECT_ROOT", str(tmp_path))
        _set_workspace_cfg(monkeypatch, "workspace")
        assert paths_mod.get_workspace_root() == str(tmp_path / "workspace")

    def test_custom_subdir(self, monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
        """自定义相对子目录：项目根为基准。"""
        from core import path as path_mod
        monkeypatch.setattr(path_mod, "_PROJECT_ROOT", str(tmp_path))
        _set_workspace_cfg(monkeypatch, "data/ws")
        assert paths_mod.get_workspace_root() == str(tmp_path / "data" / "ws")

    def test_external_absolute_path_allowed(
            self, monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
        """项目外绝对路径是合法用法（独立工作区），原样通过。"""
        _set_workspace_cfg(monkeypatch, str(tmp_path / "external-ws"))
        assert paths_mod.get_workspace_root() == str(tmp_path / "external-ws")

    @pytest.mark.parametrize("dangerous", [".", "~", "/"])
    def test_project_root_guard_falls_back(
            self, monkeypatch: pytest.MonkeyPatch, tmp_path,
            dangerous: str) -> None:
        """防呆守卫：配置覆盖项目根（或其祖先）时回退默认 workspace/。

        项目根与 HOME 一并指到临时目录（密闭）：用例不得依赖检出位置——
        仓库不在用户目录下时 ~ 不是项目根祖先，守卫语义随位置漂移。
        """
        from core import path as path_mod

        proj = str(tmp_path / "proj")
        monkeypatch.setattr(path_mod, "_PROJECT_ROOT", proj)
        monkeypatch.setenv("HOME", str(tmp_path))
        _set_workspace_cfg(monkeypatch, dangerous)
        assert paths_mod.get_workspace_root() == os.path.join(proj, "workspace")


class TestResolveWorkspacePath:
    def test_relative_path_anchors_root(self) -> None:
        """相对路径锚定 workspace 根（与进程 cwd 无关）。"""
        root = "/tmp/fake-ws-anchor"
        assert paths_mod.resolve_workspace_path("a/b.txt", root) == \
            os.path.join(root, "a", "b.txt")

    def test_workspace_prefix_stripped(self) -> None:
        """误带 workspace 前缀剥离防双重嵌套。"""
        root = "/tmp/fake-ws-anchor"
        assert paths_mod.resolve_workspace_path("fake-ws-anchor/x.txt", root) == \
            os.path.join(root, "x.txt")


class TestPythonExecCwdAnchor:
    def test_cwd_anchored_even_without_sandbox(
            self, monkeypatch: pytest.MonkeyPatch) -> None:
        """沙箱关闭时 python_exec 的 cwd 也锚定 workspace（事故根因回归）。"""
        from core.config import ConfigManager
        from entities.filesystem import paths as _paths
        from entities.filesystem import tools as fs_tools

        captured: Dict[str, Any] = {}

        class _FakeResult:
            returncode = 0
            stdout = ""
            stderr = ""

        def _fake_run(*args: Any, **kwargs: Any) -> _FakeResult:
            captured.update(kwargs)
            return _FakeResult()

        monkeypatch.setattr("subprocess.run", _fake_run)
        monkeypatch.setattr(
            ConfigManager, "get",
            staticmethod(lambda key, default=None: False if key == "sandbox_enabled" else default),
        )
        fs_tools.python_exec("print('hi')")
        assert captured["cwd"] == _paths.get_workspace_root()
