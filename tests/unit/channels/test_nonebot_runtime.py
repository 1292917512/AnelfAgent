"""NoneBot 桥接运行时测试：venv/安装命令构建、worker 文件渲染、粘性路由。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from channels.nonebot_bridge import runtime as nb_runtime
from channels.nonebot_bridge.adapter import NoneBotBridgeChannel
from channels.nonebot_bridge.runtime import (
    build_install_command,
    build_uninstall_command,
    build_venv_create_command,
    build_worker_files,
)
from core.path import ConfigPaths


@pytest.fixture()
def isolated_nonebot_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """把 NONEBOT_DIR 覆盖到临时目录。"""
    nonebot_dir = tmp_path / "nonebot"
    monkeypatch.setattr(ConfigPaths, "NONEBOT_DIR", str(nonebot_dir))
    return nonebot_dir


class TestCommandBuilding:
    """uv 存在/缺失两分支的命令构建。"""

    def test_venv_create_with_uv(self, tmp_path: Path) -> None:
        cmd = build_venv_create_command("/usr/local/bin/uv", "/usr/bin/python3", tmp_path)
        assert cmd == ["/usr/local/bin/uv", "venv", "--python", "/usr/bin/python3", str(tmp_path)]

    def test_venv_create_without_uv(self, tmp_path: Path) -> None:
        cmd = build_venv_create_command(None, "/usr/bin/python3", tmp_path)
        assert cmd[:3] == ["/usr/local/bin/python", "-m", "venv"] or cmd[1:3] == ["-m", "venv"]
        assert cmd[-1] == str(tmp_path)

    def test_install_with_uv(self, tmp_path: Path) -> None:
        cmd = build_install_command("uv", tmp_path / "bin/python", ["nonebot-adapter-qq"])
        assert cmd == ["uv", "pip", "install", "--python", str(tmp_path / "bin/python"),
                       "nonebot-adapter-qq"]

    def test_install_without_uv_uses_venv_pip(self, tmp_path: Path) -> None:
        python = tmp_path / "bin/python"
        cmd = build_install_command(None, python, ["pkg"])
        assert cmd == [str(python), "-m", "pip", "install", "pkg"]

    def test_uninstall_carries_yes_flag(self, tmp_path: Path) -> None:
        python = tmp_path / "bin/python"
        assert build_uninstall_command(None, python, ["pkg"]) == [
            str(python), "-m", "pip", "uninstall", "-y", "pkg",
        ]
        assert "-y" in build_uninstall_command("uv", python, ["pkg"])


class TestWorkerFiles:
    """build_worker_files 渲染（.env / config.json）。"""

    def test_builtin_adapter_entry(self) -> None:
        files = build_worker_files({
            "adapters": ["onebot_v11"],
            "plugins": ["nonebot_plugin_status"],
            "intercept_all": False,
            "worker_host": "0.0.0.0",
            "worker_port": 9000,
        })
        cfg = json.loads(files["config.json"])
        assert cfg["adapters"] == [
            {"key": "onebot_v11", "import": "nonebot.adapters.onebot.v11", "class": "Adapter"},
        ]
        assert cfg["plugins"] == ["nonebot_plugin_status"]
        assert cfg["intercept_all"] is False
        assert cfg["wire_version"] == 3

    def test_env_file_content(self) -> None:
        files = build_worker_files({
            "adapters": ["telegram"],
            "plugins": [],
            "nonebot_env": {"TELEGRAM_BOTS": '[{"token": "t"}]'},
            "intercept_all": True,
            "worker_host": "127.0.0.1",
            "worker_port": 8198,
        })
        env = files[".env"]
        assert "DRIVER=~fastapi+~aiohttp" in env
        assert "HOST=127.0.0.1" in env
        assert "PORT=8198" in env
        assert 'TELEGRAM_BOTS=[{"token": "t"}]' in env

    def test_registry_adapter_passthrough(self) -> None:
        files = build_worker_files({"adapters": ["nonebot.adapters.ding"]})
        cfg = json.loads(files["config.json"])
        assert cfg["adapters"] == [
            {"key": "nonebot.adapters.ding", "import": "nonebot.adapters.ding", "class": "Adapter"},
        ]

    def test_unknown_adapter_skipped(self) -> None:
        files = build_worker_files({"adapters": ["not_an_adapter"]})
        cfg = json.loads(files["config.json"])
        assert cfg["adapters"] == []


class TestVenvMarker:
    """is_venv_ready 标记逻辑。"""

    def test_not_ready_when_missing(self, isolated_nonebot_dir: Path) -> None:
        rt = nb_runtime.NoneBotRuntime()
        assert rt.is_venv_ready() is False

    def test_ready_when_marker_matches(self, isolated_nonebot_dir: Path) -> None:
        venv = isolated_nonebot_dir / "venv" / "bin"
        venv.mkdir(parents=True)
        (venv / "python").write_text("", encoding="utf-8")
        (isolated_nonebot_dir / ".venv_baseline").write_text(
            "\n".join(nb_runtime._BASELINE_PACKAGES), encoding="utf-8"
        )
        rt = nb_runtime.NoneBotRuntime()
        assert rt.is_venv_ready() is True

    def test_stale_marker_not_ready(self, isolated_nonebot_dir: Path) -> None:
        venv = isolated_nonebot_dir / "venv" / "bin"
        venv.mkdir(parents=True)
        (venv / "python").write_text("", encoding="utf-8")
        (isolated_nonebot_dir / ".venv_baseline").write_text("old-spec", encoding="utf-8")
        rt = nb_runtime.NoneBotRuntime()
        assert rt.is_venv_ready() is False


class TestLogRing:
    """日志环容量与尾部读取。"""

    def test_tail_logs(self, isolated_nonebot_dir: Path) -> None:
        rt = nb_runtime.NoneBotRuntime()
        for i in range(10):
            rt.append_log(f"line-{i}")
        assert rt.tail_logs(3) == ["line-7", "line-8", "line-9"]
        assert rt.tail_logs(0) == []


def _bare_channel() -> NoneBotBridgeChannel:
    """跳过 __init__ 的裸实例（避免配置加载与全局注册副作用）。"""
    ch = NoneBotBridgeChannel.__new__(NoneBotBridgeChannel)
    ch._sticky_bot = {}
    ch._sticky_group = {}
    return ch


class TestStickyRouting:
    """粘性路由表与 Bot 选择。"""

    def test_set_sticky_and_group_lookup(self) -> None:
        ch = _bare_channel()
        NoneBotBridgeChannel._set_sticky(ch._sticky_group, "70001", True)
        NoneBotBridgeChannel._set_sticky(ch._sticky_group, "20002", False)
        assert ch.is_known_group("70001") is True
        assert ch.is_known_group("20002") is False
        assert ch.is_known_group("unknown") is False

    def test_resolve_bot_prefers_explicit(self) -> None:
        ch = _bare_channel()
        NoneBotBridgeChannel._set_sticky(ch._sticky_bot, "70001", "10001")
        assert ch._resolve_bot_id("70001") == "10001"
        assert ch._resolve_bot_id("70001", bot_id="10002") == "10002"
        assert ch._resolve_bot_id("never-seen") == ""

    def test_set_sticky_refreshes_recency(self) -> None:
        ch = _bare_channel()
        NoneBotBridgeChannel._set_sticky(ch._sticky_bot, "k", "bot-a")
        NoneBotBridgeChannel._set_sticky(ch._sticky_bot, "k", "bot-b")
        assert ch._sticky_bot["k"] == "bot-b"

    def test_sticky_cap_prunes_oldest(self) -> None:
        from channels.nonebot_bridge.adapter import _STICKY_CAP

        ch = _bare_channel()
        store = ch._sticky_bot
        for i in range(_STICKY_CAP + 100):
            NoneBotBridgeChannel._set_sticky(store, f"key-{i}", "bot")
        assert len(store) <= _STICKY_CAP
        assert "key-0" not in store
        assert f"key-{_STICKY_CAP + 99}" in store
