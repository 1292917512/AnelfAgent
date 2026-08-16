"""NoneBot 服务层测试：商店搜索 / 快照兜底 / 配置读写（不触网）。"""

from __future__ import annotations

from pathlib import Path
from typing import List

import pytest

from services.nonebot import NoneBotService, _read_channel_config, _write_channel_config


@pytest.fixture()
def isolated_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """把频道配置路径与商店快照目录隔离到临时目录。"""
    cfg_path = tmp_path / "channel_config.json"
    monkeypatch.setattr("services.nonebot._channel_config_path", lambda: cfg_path)

    from core.path import ConfigPaths

    monkeypatch.setattr(ConfigPaths, "NONEBOT_DIR", str(tmp_path / "nonebot"))
    return cfg_path


_PLUGINS: List[dict] = [
    {
        "module_name": "nonebot_plugin_status",
        "project_link": "nonebot-plugin-status",
        "name": "服务器状态",
        "desc": "通过戳一戳获取服务器状态",
        "author": "yanyongyu",
        "tags": [{"label": "server"}],
        "is_official": True,
        "type": "application",
        "valid": True,
        "version": "0.9.0",
    },
    {
        "module_name": "haruka_bot",
        "project_link": "haruka-bot",
        "name": "haruka_bot",
        "desc": "将B站UP主的动态和直播信息推送至QQ",
        "author": "SK-415",
        "tags": [{"label": "bilibili"}],
        "is_official": False,
        "type": "application",
        "valid": False,
        "version": "1.6.0",
    },
]


@pytest.fixture()
def store_plugins(monkeypatch: pytest.MonkeyPatch) -> None:
    """商店拉取替换为固定数据（清空类级缓存避免跨用例污染）。"""
    async def _fake_fetch(url: str) -> List[dict]:
        return list(_PLUGINS)

    monkeypatch.setattr(NoneBotService, "_fetch_json", staticmethod(_fake_fetch))
    NoneBotService._plugins_cache = None
    NoneBotService._plugins_fetched_at = 0.0
    yield
    NoneBotService._plugins_cache = None
    NoneBotService._plugins_fetched_at = 0.0


class TestStoreSearch:
    """search_store_plugins 评分与过滤。"""

    @pytest.mark.asyncio
    async def test_exact_module_ranks_first(self, store_plugins) -> None:
        results = await NoneBotService().search_store_plugins("haruka_bot", limit=5)
        assert results[0]["module_name"] == "haruka_bot"

    @pytest.mark.asyncio
    async def test_keyword_in_desc(self, store_plugins) -> None:
        results = await NoneBotService().search_store_plugins("服务器")
        assert len(results) == 1
        assert results[0]["module_name"] == "nonebot_plugin_status"

    @pytest.mark.asyncio
    async def test_tag_keyword(self, store_plugins) -> None:
        results = await NoneBotService().search_store_plugins("bilibili")
        assert [r["module_name"] for r in results] == ["haruka_bot"]

    @pytest.mark.asyncio
    async def test_no_match_returns_empty(self, store_plugins) -> None:
        assert await NoneBotService().search_store_plugins("不存在的关键词xyz") == []

    @pytest.mark.asyncio
    async def test_empty_keyword_returns_head(self, store_plugins) -> None:
        results = await NoneBotService().search_store_plugins("", limit=1)
        assert len(results) == 1


class TestSnapshotFallback:
    """注册表不可达时的磁盘快照兜底。"""

    @pytest.mark.asyncio
    async def test_snapshot_saved_and_loaded(self, isolated_config, monkeypatch: pytest.MonkeyPatch) -> None:
        async def _fake_fetch(url: str) -> List[dict]:
            return list(_PLUGINS)

        monkeypatch.setattr(NoneBotService, "_fetch_json", staticmethod(_fake_fetch))
        NoneBotService._adapters_cache = None
        NoneBotService._adapters_fetched_at = 0.0

        svc = NoneBotService()
        data = await svc.fetch_store_adapters()
        assert data == _PLUGINS

        # 拉取失败时回退磁盘快照
        async def _fail_fetch(url: str) -> List[dict]:
            return None

        monkeypatch.setattr(NoneBotService, "_fetch_json", staticmethod(_fail_fetch))
        NoneBotService._adapters_cache = None
        NoneBotService._adapters_fetched_at = 0.0

        fallback = await svc.fetch_store_adapters()
        assert fallback == _PLUGINS


class TestChannelConfigIo:
    """频道配置读写（隔离到临时路径）。"""

    def test_write_then_read(self, isolated_config: Path) -> None:
        _write_channel_config({"enabled": True, "adapters": ["onebot_v11"]})
        cfg = _read_channel_config()
        assert cfg["enabled"] is True
        assert cfg["adapters"] == ["onebot_v11"]

    def test_read_missing_returns_empty(self, isolated_config: Path) -> None:
        assert _read_channel_config() == {}

    def test_read_corrupt_returns_empty(self, isolated_config: Path) -> None:
        isolated_config.write_text("{corrupt", encoding="utf-8")
        assert _read_channel_config() == {}


class TestListMutation:
    """plugins/adapters 列表增删辅助。"""

    def test_append_unique(self) -> None:
        cfg: dict = {"plugins": ["a"]}
        NoneBotService._append_unique(cfg, "plugins", "b")
        NoneBotService._append_unique(cfg, "plugins", "a")
        assert cfg["plugins"] == ["a", "b"]

    def test_remove_item(self) -> None:
        cfg: dict = {"adapters": ["onebot_v11", "telegram"]}
        NoneBotService._remove_item(cfg, "adapters", "onebot_v11")
        assert cfg["adapters"] == ["telegram"]
        NoneBotService._remove_item(cfg, "adapters", "missing")
        assert cfg["adapters"] == ["telegram"]

    def test_append_creates_list(self) -> None:
        cfg: dict = {}
        NoneBotService._append_unique(cfg, "plugins", "x")
        assert cfg["plugins"] == ["x"]
