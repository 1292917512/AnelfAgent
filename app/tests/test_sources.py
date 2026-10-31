"""镜像覆盖只作用于安装子进程，不修改项目索引、其他工具来源或全局配置。"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from app.environment import Environment
from app.manager import Manager
from app.repository import describe, origin, selected_origin
from app.settings import DOWNLOADS, PROXY_VARIABLES, Layout, Settings, apply_proxy_env, download_profile


@pytest.mark.parametrize("source", list(DOWNLOADS["pypi_sources"]))
def test_package_source_is_independent_and_read_only(tmp_path: Path, source: str, monkeypatch: pytest.MonkeyPatch) -> None:
    layout = Layout(tmp_path)
    layout.save_settings(Settings(download_source="global", pypi_source=source))
    original = b'[tool.uv]\nindex-url="https://example.invalid/simple"\n'
    project = tmp_path / "pyproject.toml"; project.write_bytes(original)
    profile = download_profile(layout.load_settings())
    assert profile["pypi"] == DOWNLOADS["pypi_sources"][source]["url"]
    assert profile["node"] == DOWNLOADS["global"]["node"]
    assert profile["python"] == DOWNLOADS["global"]["python"]
    for key in DOWNLOADS["uv_index_environment"]:
        monkeypatch.setenv(key, "existing-value")
    environment = Environment(layout, lambda _: None)
    isolated = environment.environ(isolated_index=True)
    assert all(key not in isolated and os.environ[key] == "existing-value" for key in DOWNLOADS["uv_index_environment"])
    assert isolated["UV_CACHE_DIR"] == environment.environ()["UV_CACHE_DIR"]
    assert project.read_bytes() == original


def test_existing_settings_keep_automatic_source() -> None:
    assert Settings.parse({"download_source": "china"}).pypi_source == "auto"
    assert download_profile(Settings(download_source="china"))["pypi"] == DOWNLOADS["china"]["pypi"]
    with pytest.raises(ValueError, match="Unknown uv"):
        Settings.parse({"pypi_source": "https://example.invalid"})


def test_repository_selection_preserves_existing_urls_and_protects_custom_forks() -> None:
    old = "git@github.com:1292917512/AnelfAgent.git"
    assert selected_origin(old, "auto") == old
    assert selected_origin(old, "gitee") == "https://gitee.com/eflyz/AnelfAgent.git"
    assert selected_origin("ssh://git@gitee.com/eflyz/AnelfAgent.git", "github") == "https://github.com/1292917512/AnelfAgent.git"
    fork = "https://github.com/my-team/custom-agent.git"
    assert selected_origin(fork, "auto") == selected_origin(fork, "github") == fork
    with pytest.raises(ValueError, match="自定义仓库"):
        selected_origin(fork, "gitee")
    assert describe("https://user:secret@github.com/1292917512/AnelfAgent.git?secret=x")["origin"] == "https://github.com/1292917512/AnelfAgent.git"


def test_manager_detects_and_switches_origin_without_changing_worktree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    original = "git@github.com:1292917512/AnelfAgent.git"
    subprocess.run(["git", "-C", str(tmp_path), "remote", "add", "origin", original], check=True)
    local = tmp_path / "keep.txt"
    local.write_text("uncommitted")
    manager = Manager(Layout(tmp_path))
    assert manager.status()["resolved_source"] == "github"
    manager.configure_repository("auto")
    assert origin(tmp_path, manager.environment.tool("git")) == original
    manager.configure_repository("gitee")
    assert manager.status()["repository"]["origin"] == "https://gitee.com/eflyz/AnelfAgent.git"
    manager.configure_repository("github")
    assert origin(tmp_path, manager.environment.tool("git")) == "https://github.com/1292917512/AnelfAgent.git"
    assert local.read_text() == "uncommitted"
    with monkeypatch.context() as patch:
        def fail_save(_self: Layout, _settings: Settings) -> None:
            raise OSError("Disk unavailable")
        patch.setattr(Layout, "save_settings", fail_save)
        with pytest.raises(OSError, match="Disk unavailable"):
            manager.configure_repository("gitee")
    assert manager.settings.source == "github"
    assert origin(tmp_path, manager.environment.tool("git")) == "https://github.com/1292917512/AnelfAgent.git"
    manager.configure_repository("auto")
    assert manager.settings.source == "auto"


def test_proxy_settings_validation() -> None:
    assert Settings.parse({}).proxy == ""
    assert Settings.parse({"proxy": "http://127.0.0.1:7890"}).proxy == "http://127.0.0.1:7890"
    assert Settings.parse({"proxy": "https://proxy.example:8080"}).proxy
    for invalid in ("ftp://127.0.0.1:21", "127.0.0.1:7890", "http://a b", 5, " http://x"):
        with pytest.raises(ValueError):
            Settings.parse({"proxy": invalid})


def test_proxy_environment_scoped_to_installer_children(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    layout = Layout(tmp_path)
    layout.save_settings(Settings(proxy="http://127.0.0.1:7890"))
    environment = Environment(layout, lambda _: None)
    env = environment.environ()
    assert all(env[key] == "http://127.0.0.1:7890" for key in PROXY_VARIABLES)
    assert env["NO_PROXY"] == "localhost,127.0.0.1,::1"
    # 运行态应用入口不继承安装代理，也不被宿主环境残留变量污染。
    runtime = environment.environ(proxy=False)
    assert all(key not in runtime for key in (*PROXY_VARIABLES, "NO_PROXY"))
    monkeypatch.setenv("HTTPS_PROXY", "http://ambient:1")
    layout.save_settings(Settings())
    assert all(key not in environment.environ() for key in PROXY_VARIABLES)


def test_apply_proxy_env_updates_process_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALL_PROXY", "http://stale:1")
    apply_proxy_env(Settings(proxy="http://127.0.0.1:7890"))
    assert os.environ["HTTPS_PROXY"] == "http://127.0.0.1:7890"
    apply_proxy_env(Settings())
    assert all(key not in os.environ for key in (*PROXY_VARIABLES, "NO_PROXY"))
