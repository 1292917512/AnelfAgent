"""agent/channel/config.py 频道配置统一接入的单元测试。

覆盖：CONFIG_MODEL 扫描注册（仅子类声明字段）、ChannelConfigStore 频道目录
文件存储（读写路由 / env 覆盖 / 原子落盘 / 文件级变更 diff 上报）、
BaseChannel 从 ConfigManager 物化与监听热更。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import Field

import agent.channel.config as channel_config_mod
from agent.channel.config import (
    ChannelConfigStore,
    register_channel_schemas,
    set_channel_config,
)
from core.config import ConfigManager, ConfigRegistry


def _make_channel_dir(root: Path, channel_id: str, *, with_config_py: bool = True,
                      values: dict | None = None) -> Path:
    """构造假频道目录：adapter.py + 可选 config.py（CONFIG_MODEL）+ 可选配置文件。"""
    d = root / channel_id
    d.mkdir(parents=True)
    (d / "adapter.py").write_text("# fake", "utf-8")
    if with_config_py:
        (d / "config.py").write_text(
            "from pydantic import Field\n"
            "from agent.channel.base import ChannelConfig\n"
            "class Cfg(ChannelConfig):\n"
            "    enabled: bool = Field(default=False, description='启用')\n"
            "    token: str = Field(default='', description='令牌',"
            " json_schema_extra={'value_type': 'password'})\n"
            "CONFIG_MODEL = Cfg\n",
            "utf-8",
        )
    if values is not None:
        (d / "channel_config.json").write_text(json.dumps(values), "utf-8")
    return d


@pytest.fixture()
def isolated_channels(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """隔离频道目录。"""
    root = tmp_path / "channels"
    root.mkdir()
    monkeypatch.setattr(channel_config_mod, "channels_dir", lambda: root)
    return root


class TestRegisterChannelSchemas:
    def test_registers_declared_fields_only(self, isolated_channels: Path) -> None:
        _make_channel_dir(isolated_channels, "ctest")
        assert register_channel_schemas() == ["ctest"]
        assert ConfigRegistry.get_item("ctest_enabled") is not None
        assert ConfigRegistry.get_item("ctest_token") is not None
        # ChannelConfig 基类通用字段不进配置面
        assert ConfigRegistry.get_item("ctest_command_prefix") is None
        assert ConfigManager.get("ctest_enabled") is False

    def test_dir_without_config_py_skipped(self, isolated_channels: Path) -> None:
        d = isolated_channels / "bare"
        d.mkdir()
        (d / "adapter.py").write_text("# fake", "utf-8")
        assert register_channel_schemas() == []

    def test_values_load_from_channel_file(self, isolated_channels: Path) -> None:
        """频道目录文件是值的存储位置：注册后统一配置面读到文件值。"""
        _make_channel_dir(isolated_channels, "ctest",
                          values={"enabled": True, "token": "file-token"})
        register_channel_schemas()
        assert ConfigManager.get("ctest_enabled") is True
        assert ConfigManager.get("ctest_token") == "file-token"

    def test_writes_landed_in_channel_file(self, isolated_channels: Path) -> None:
        """统一配置面写入路由到频道目录文件（app_config 不出现频道键）。"""
        _make_channel_dir(isolated_channels, "ctest", values={"enabled": False})
        register_channel_schemas()
        set_channel_config("ctest", token="new-token")
        data = json.loads(
            (isolated_channels / "ctest" / "channel_config.json").read_text("utf-8"),
        )
        assert data["token"] == "new-token"
        assert data["enabled"] is False
        # app_config 内存层不含频道键（值由频道目录文件持有）
        assert "ctest_token" not in ConfigManager.get_all()


class TestChannelConfigStore:
    def test_env_override(self, isolated_channels: Path,
                          monkeypatch: pytest.MonkeyPatch) -> None:
        store = ChannelConfigStore("ctest", isolated_channels / "ctest" / "channel_config.json")
        store.load()
        monkeypatch.setenv("ANELF_CTEST_TOKEN", "env-token")
        assert store.get("ctest_token") == "env-token"
        assert store.has("ctest_token") is True

    def test_unprefixed_file_format(self, tmp_path: Path) -> None:
        """文件保持无前缀字段名（模块自持有格式）。"""
        store = ChannelConfigStore("ctest", tmp_path / "channel_config.json")
        store.set("ctest_enabled", True)
        store.save()
        assert json.loads((tmp_path / "channel_config.json").read_text("utf-8")) == {
            "enabled": True,
        }

    def test_reload_notify_diff(self, tmp_path: Path) -> None:
        """文件级外部变更经 diff 上报变更监听（无变化不上报）。"""
        path = tmp_path / "channel_config.json"
        path.write_text('{"enabled": false, "token": "a"}', "utf-8")
        store = ChannelConfigStore("ctest", path)
        ConfigManager.register_store("ctest_", store)
        fired: list = []
        ConfigManager.add_listener("ctest_", lambda k, v: fired.append(k))

        store.reload_notify()  # 无变化
        assert fired == []

        path.write_text('{"enabled": true, "token": "a"}', "utf-8")
        store.reload_notify()
        assert fired == ["ctest_enabled"]
        assert ConfigManager.get("ctest_enabled") is True

    def test_corrupt_file_tolerated(self, tmp_path: Path) -> None:
        path = tmp_path / "channel_config.json"
        path.write_text("{broken", "utf-8")
        store = ChannelConfigStore("ctest", path)
        store.load()
        assert store.get("ctest_enabled", False) is False


class TestChannelMaterialization:
    def test_materialize_and_hot_reload(self, isolated_channels: Path) -> None:
        from agent.channel.base import BaseChannel, ChannelConfig

        class Cfg(ChannelConfig):
            enabled: bool = Field(default=False, description="启用")
            greeting: str = Field(default="hi", description="问候")

        class C(BaseChannel[Cfg]):
            channel_id = "ctest"
            display_name = "CTest"
            capabilities = set()
            metadata = None  # type: ignore[assignment]
            _Configs = Cfg

            async def start(self) -> None: ...
            async def stop(self) -> None: ...
            async def forward_message(self, request): ...
            async def get_self_info(self): ...
            async def get_channel_info(self, channel_id): ...
            async def health_check(self): ...

        _make_channel_dir(isolated_channels, "ctest", values={"greeting": "hey"})
        register_channel_schemas()
        ch = C()
        assert ch.config.greeting == "hey"
        set_channel_config("ctest", greeting="hello")
        assert ch.config.greeting == "hello"  # 变更监听即时热更
