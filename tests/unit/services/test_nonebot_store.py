"""NoneBot 服务层测试：商店搜索 / 快照兜底 / 配置读写（不触网）。"""

from __future__ import annotations

import time
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


class TestConfigAtomicOps:
    """set_config_value / get_config_masked / enable-disable（隔离配置路径）。"""

    def test_set_top_level_bool(self, isolated_config: Path) -> None:
        result = NoneBotService().set_config_value("intercept_all", "true")
        assert result["success"] is True
        assert _read_channel_config()["intercept_all"] is True

    def test_set_top_level_int_coercion(self, isolated_config: Path) -> None:
        result = NoneBotService().set_config_value("worker_port", "9000")
        assert result["success"] is True
        assert _read_channel_config()["worker_port"] == 9000

    def test_set_list_from_csv(self, isolated_config: Path) -> None:
        result = NoneBotService().set_config_value("adapters", "onebot_v11, telegram")
        assert result["success"] is True
        assert _read_channel_config()["adapters"] == ["onebot_v11", "telegram"]

    def test_set_invalid_bool_rejected(self, isolated_config: Path) -> None:
        result = NoneBotService().set_config_value("intercept_all", "maybe")
        assert result["success"] is False
        assert "intercept_all" not in _read_channel_config()

    def test_unknown_key_rejected(self, isolated_config: Path) -> None:
        result = NoneBotService().set_config_value("no_such_key", "1")
        assert result["success"] is False
        assert "未知配置项" in result["error"]

    def test_set_env_key_and_remove(self, isolated_config: Path) -> None:
        svc = NoneBotService()
        assert svc.set_config_value("nonebot_env.TELEGRAM_BOTS", '[{"token": "t"}]')["success"]
        assert _read_channel_config()["nonebot_env"]["TELEGRAM_BOTS"] == '[{"token": "t"}]'
        # 空值删除
        assert svc.set_config_value("nonebot_env.TELEGRAM_BOTS", "")["success"]
        assert "TELEGRAM_BOTS" not in _read_channel_config()["nonebot_env"]

    def test_get_config_masked(self, isolated_config: Path) -> None:
        _write_channel_config({
            "nonebot_env": {
                "ONEBOT_ACCESS_TOKEN": "secret-value",
                "COMMAND_START": '["/"]',
            }
        })
        cfg = NoneBotService().get_config_masked()
        assert cfg["nonebot_env"]["ONEBOT_ACCESS_TOKEN"] == "********"
        assert cfg["nonebot_env"]["COMMAND_START"] == '["/"]'


class TestEnableDisable:
    """适配器/插件启停（仅调整加载列表）。"""

    def test_adapter_enable_disable(self, isolated_config: Path) -> None:
        svc = NoneBotService()
        result = svc.set_adapter_enabled("onebot_v11", True)
        assert result["success"] and result["adapters"] == ["onebot_v11"]
        result = svc.set_adapter_enabled("telegram", True)
        assert result["adapters"] == ["onebot_v11", "telegram"]
        result = svc.set_adapter_enabled("onebot_v11", False)
        assert result["adapters"] == ["telegram"]

    def test_plugin_enable_disable(self, isolated_config: Path) -> None:
        svc = NoneBotService()
        assert svc.set_plugin_enabled("p_a", True)["plugins"] == ["p_a"]
        assert svc.set_plugin_enabled("p_b", True)["plugins"] == ["p_a", "p_b"]
        assert svc.set_plugin_enabled("p_a", False)["plugins"] == ["p_b"]


class TestCoerceConfigValue:
    """配置值类型强转。"""

    def test_bool_variants(self) -> None:
        assert NoneBotService._coerce_config_value(bool, True) is True
        assert NoneBotService._coerce_config_value(bool, "false") is False
        assert NoneBotService._coerce_config_value(bool, "maybe") is None

    def test_int_variants(self) -> None:
        assert NoneBotService._coerce_config_value(int, "8080") == 8080
        assert NoneBotService._coerce_config_value(int, "abc") is None

    def test_list_variants(self) -> None:
        assert NoneBotService._coerce_config_value(list, ["a", 1]) == ["a", "1"]
        assert NoneBotService._coerce_config_value(list, "a, b") == ["a", "b"]
        assert NoneBotService._coerce_config_value(list, "  ") == []

    def test_str_passthrough(self) -> None:
        assert NoneBotService._coerce_config_value(str, 123) == "123"


class TestEnsureAdaptersLoaded:
    """ensure_adapters_loaded：缓存短路 / 尝试退避 / 冷启动拉取。"""

    @pytest.fixture(autouse=True)
    def _reset_adapters_state(self):
        NoneBotService._adapters_cache = None
        NoneBotService._adapters_fetched_at = 0.0
        NoneBotService._adapters_attempt_at = 0.0
        yield
        NoneBotService._adapters_cache = None
        NoneBotService._adapters_fetched_at = 0.0
        NoneBotService._adapters_attempt_at = 0.0

    @pytest.mark.asyncio
    async def test_fresh_cache_skips_fetch(self) -> None:
        async def _must_not_fetch(url: str) -> List[dict]:
            raise AssertionError("缓存新鲜时不应发起拉取")

        NoneBotService._adapters_cache = list(_PLUGINS)
        NoneBotService._adapters_fetched_at = time.time()
        NoneBotService._adapters_attempt_at = 0.0
        original = NoneBotService._fetch_json
        NoneBotService._fetch_json = staticmethod(_must_not_fetch)
        try:
            await NoneBotService().ensure_adapters_loaded()
        finally:
            NoneBotService._fetch_json = original

    @pytest.mark.asyncio
    async def test_recent_attempt_backoff(self, monkeypatch: pytest.MonkeyPatch) -> None:
        called = []

        async def _fetch(url: str) -> List[dict]:
            called.append(url)
            return list(_PLUGINS)

        monkeypatch.setattr(NoneBotService, "_fetch_json", staticmethod(_fetch))
        NoneBotService._adapters_attempt_at = time.time() - 5.0  # 5s 前刚尝试过
        await NoneBotService().ensure_adapters_loaded()
        assert called == []  # 退避窗口内不重复拉取

    @pytest.mark.asyncio
    async def test_cold_start_fetches_and_caches(self, monkeypatch: pytest.MonkeyPatch) -> None:
        async def _fetch(url: str) -> List[dict]:
            return list(_PLUGINS)

        monkeypatch.setattr(NoneBotService, "_fetch_json", staticmethod(_fetch))
        await NoneBotService().ensure_adapters_loaded()
        assert NoneBotService._adapters_cache == _PLUGINS
        assert NoneBotService._adapters_attempt_at > 0

    @pytest.mark.asyncio
    async def test_fetch_failure_tolerated(self, monkeypatch: pytest.MonkeyPatch) -> None:
        async def _fetch(url: str) -> None:
            return None

        monkeypatch.setattr(NoneBotService, "_fetch_json", staticmethod(_fetch))
        await NoneBotService().ensure_adapters_loaded()  # 不抛异常
        assert NoneBotService._adapters_cache is None


class TestListAdaptersMerge:
    """list_adapters 的内置 ∪ 注册表合并与去重。"""

    def test_registry_adapters_merged_and_deduped(
        self, isolated_config, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        registry = [
            {"module_name": "nonebot.adapters.ding", "project_link": "nonebot-adapter-ding",
             "name": "钉钉", "version": "2.0.0", "homepage": ""},
            # 与内置重复（console）→ 去重
            {"module_name": "nonebot.adapters.console", "project_link": "nonebot-adapter-console",
             "name": "Console", "version": "1.0.0", "homepage": ""},
        ]
        NoneBotService._adapters_cache = registry
        NoneBotService._adapters_fetched_at = time.time()
        try:
            adapters = NoneBotService().list_adapters()
        finally:
            NoneBotService._adapters_cache = None
            NoneBotService._adapters_fetched_at = 0.0

        by_key = {a["key"]: a for a in adapters}
        assert by_key["ding"]["builtin"] is False
        assert by_key["ding"]["package"] == "nonebot-adapter-ding"
        assert by_key["ding"]["version"] == "2.0.0"
        # 内置 console 保留一条且仍是 builtin
        consoles = [a for a in adapters if a["key"] == "console"]
        assert len(consoles) == 1 and consoles[0]["builtin"] is True
        # venv 未就绪时 installed 全 False，enabled 来自配置
        assert by_key["onebot_v11"]["installed"] is False


class TestInstallFromSource:
    """git/本地路径安装：spec 规范化、溯源记录、卸载分发名推导。"""

    @pytest.mark.asyncio
    async def test_install_plugin_git_records_spec(
        self, isolated_config, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        installed: list = []

        async def _fake_install(_self, spec: str, editable: bool = False) -> dict:
            installed.append((spec, editable))
            return {"success": True}

        localized: list = []

        async def _fake_localize(_self, spec: str) -> str:
            localized.append(spec)
            return "/fake/local/repo"  # git 源本地化：返回本地检出路径

        monkeypatch.setattr(NoneBotService, "_install_with_source", _fake_install)
        monkeypatch.setattr(NoneBotService, "_localize_git_source", _fake_localize)
        result = await NoneBotService().install_plugin(
            "nonebot_plugin_my", source="https://github.com/me/my-plugin.git"
        )
        assert result["success"] is True
        # git 源先本地化，安装用的是本地路径
        assert localized == ["git+https://github.com/me/my-plugin.git"]
        assert installed == [("/fake/local/repo", False)]
        cfg = _read_channel_config()
        assert cfg["plugins"] == ["nonebot_plugin_my"]
        # 溯源记录原始 git spec（resync 按它拉取）
        assert cfg["package_specs"]["nonebot_plugin_my"] == "git+https://github.com/me/my-plugin.git"

    @pytest.mark.asyncio
    async def test_install_plugin_local_editable(
        self, isolated_config, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        installed: list = []

        async def _fake_install(_self, spec: str, editable: bool = False) -> dict:
            installed.append((spec, editable))
            return {"success": True}

        monkeypatch.setattr(NoneBotService, "_install_with_source", _fake_install)
        repo = tmp_path / "my-plugin"
        result = await NoneBotService().install_plugin(
            "nonebot_plugin_my", source=str(repo), editable=True
        )
        assert result["success"] is True
        assert installed == [(str(repo), True)]

    @pytest.mark.asyncio
    async def test_uninstall_plugin_derives_dist_from_spec(
        self, isolated_config, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _write_channel_config({
            "plugins": ["nonebot_plugin_my"],
            "package_specs": {"nonebot_plugin_my": "git+https://github.com/me/my-plugin.git"},
        })
        uninstalled: list = []

        class _FakeRT:
            async def list_installed_packages(self):
                return [{"name": "my-plugin", "version": "0.1.0"}]

            async def uninstall_packages(self, packages, **kwargs):
                uninstalled.append(packages)
                return {"success": True}

        import channels.nonebot_bridge.runtime as rt_mod

        async def _no_store(_self, module_name: str) -> None:
            return None  # 不触网：溯源 spec 推导不依赖商店

        monkeypatch.setattr(NoneBotService, "_find_store_plugin", _no_store)
        monkeypatch.setattr(rt_mod, "get_nonebot_runtime", lambda: _FakeRT())
        result = await NoneBotService().uninstall_plugin("nonebot_plugin_my")
        assert result["success"] is True
        assert uninstalled == [["my-plugin"]]  # 由 git 源推导分发名
        cfg = _read_channel_config()
        assert cfg["plugins"] == []
        assert "nonebot_plugin_my" not in cfg.get("package_specs", {})

    @pytest.mark.asyncio
    async def test_install_adapter_git_source(
        self, isolated_config, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        installed: list = []

        async def _fake_install(_self, spec: str, editable: bool = False) -> dict:
            installed.append((spec, editable))
            return {"success": True}

        async def _ensure(_self) -> None:
            return None

        async def _fake_localize(_self, spec: str) -> str:
            return "/fake/local/adapter"

        monkeypatch.setattr(NoneBotService, "_install_with_source", _fake_install)
        monkeypatch.setattr(NoneBotService, "_localize_git_source", _fake_localize)
        monkeypatch.setattr(NoneBotService, "ensure_adapters_loaded", _ensure)
        result = await NoneBotService().install_adapter(
            "onebot_v11", enable=True, source="git+https://github.com/me/adapter-onebot.git@fix"
        )
        assert result["success"] is True
        assert installed == [("/fake/local/adapter", False)]
        cfg = _read_channel_config()
        assert "onebot_v11" in cfg["adapters"]
        assert cfg["package_specs"]["adapter:onebot_v11"].endswith("@fix")


class TestRestartAfterEnvChange:
    """resync / 升级成功后自动重启 worker。"""

    @pytest.mark.asyncio
    async def test_restart_helper_skips_when_not_running(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import channels.nonebot_bridge.runtime as rt_mod

        class _FakeRT:
            def is_process_alive(self) -> bool:
                return False

        monkeypatch.setattr(rt_mod, "get_nonebot_runtime", lambda: _FakeRT())
        # 无 channel / worker 未运行 → 不重启
        assert await NoneBotService()._restart_worker_if_alive() is False

    @pytest.mark.asyncio
    async def test_resync_restarts_when_updated(
        self, isolated_config, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _write_channel_config({
            "package_specs": {"m": "git+https://x/y.git"},
            "plugins": ["m"],
        })
        restarted = []

        async def _fake_localize(_self, spec: str) -> str:
            return "/fake/local"

        async def _fake_install(_self, spec: str, editable: bool = False, refresh: bool = False):
            return {"success": True, "refresh": refresh}

        async def _fake_restart(_self) -> bool:
            restarted.append(True)
            return True

        monkeypatch.setattr(NoneBotService, "_localize_git_source", _fake_localize)
        monkeypatch.setattr(NoneBotService, "_install_with_source", _fake_install)
        monkeypatch.setattr(NoneBotService, "_restart_worker_if_alive", _fake_restart)

        result = await NoneBotService().resync_sources()
        assert result["success"] is True
        assert result["updated"] == 1
        assert result["restarted"] is True
        assert restarted == [True]

    @pytest.mark.asyncio
    async def test_resync_no_update_no_restart(
        self, isolated_config, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _write_channel_config({"package_specs": {}})  # 无 git 源
        restarted = []

        async def _fake_restart(_self) -> bool:
            restarted.append(True)
            return True

        monkeypatch.setattr(NoneBotService, "_restart_worker_if_alive", _fake_restart)
        result = await NoneBotService().resync_sources()
        assert result["updated"] == 0 and result["restarted"] is False
        assert restarted == []


class TestDictAdaptersInService:
    """服务层对 dict 条目适配器配置的兼容（AI 手写 worker 格式场景）。"""

    def test_list_adapters_no_crash_with_dict_entries(
        self, isolated_config, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _write_channel_config({
            "adapters": [{"key": "bilibili", "import": "nonebot.adapters.bilibili", "class": "Adapter"}],
        })
        # 未命中缓存直接探测会 spawn venv python —— 隔离 NONEBOT_DIR 使 venv 未就绪
        from core.path import ConfigPaths
        monkeypatch.setattr(ConfigPaths, "NONEBOT_DIR", str(isolated_config / "nonebot"))

        adapters = NoneBotService().list_adapters()
        by_key = {a["key"]: a for a in adapters}
        assert by_key["bilibili"]["enabled"] is True  # dict 声明视为启用
        assert by_key["onebot_v11"]["enabled"] is False  # 不再 unhashable 崩溃

    def test_set_adapter_enabled_matches_dict_by_key(self, isolated_config) -> None:
        _write_channel_config({
            "adapters": [
                {"key": "bilibili", "import": "nonebot.adapters.bilibili", "class": "Adapter"},
                "onebot_v11",
            ],
        })
        svc = NoneBotService()
        result = svc.set_adapter_enabled("bilibili", False)
        assert result["adapters"] == ["onebot_v11"]  # dict 条目按 key 移除

        result = svc.set_adapter_enabled("bilibili", True)
        assert result["adapters"] == ["onebot_v11", "bilibili"]
