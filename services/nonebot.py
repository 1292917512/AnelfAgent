"""NoneBot 桥接服务层 — Web API 与 AI 工具共用的统一实现。

职责：
- 状态汇总（频道 / worker 进程 / 安装进度）与 worker 重启；
- 适配器列表（内置精选 ∪ registry.nonebot.dev 动态注册表，含安装探测）；
- 包安装/卸载（适配器与插件，落 worker venv，串行化可轮询）；
- 插件商店代理（plugins.json / adapters.json，TTL 缓存 + 磁盘快照离线兜底）；
- 桥接配置读写（channel_config.json，worker 相关变更热重启）。
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import aiohttp

from core.log import log

_REGISTRY_BASE = "https://registry.nonebot.dev"
_PLUGINS_JSON = f"{_REGISTRY_BASE}/plugins.json"
_ADAPTERS_JSON = f"{_REGISTRY_BASE}/adapters.json"
_FETCH_TIMEOUT = 20.0
_STORE_TTL = 600.0
_PROBE_TIMEOUT = 30.0

# find_spec 探测脚本（在 worker venv 中执行）
_PROBE_SCRIPT = (
    "import importlib.util, json, sys\n"
    "mods = json.loads(sys.argv[1])\n"
    "print(json.dumps({m: importlib.util.find_spec(m) is not None for m in mods}))\n"
)


def _channel_config_path() -> Path:
    return Path(__file__).parent.parent / "channels" / "nonebot_bridge" / "channel_config.json"


def _read_channel_config() -> Dict[str, Any]:
    path = _channel_config_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text("utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_channel_config(cfg: Dict[str, Any]) -> None:
    path = _channel_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), "utf-8")


def _get_channel() -> Optional[Any]:
    """获取已注册的桥接频道实例（未启用时返回 None）。"""
    try:
        from agent.channel.manager import get_channel_manager

        return get_channel_manager().get("nonebot_bridge")
    except Exception:  # noqa: BLE001 - 运行时未就绪时视为未注册
        return None


class NoneBotService:
    """NoneBot 桥接管理服务（无状态方法，商店缓存类级持有）。"""

    # 商店缓存（类级共享）
    _plugins_cache: Optional[List[Dict[str, Any]]] = None
    _adapters_cache: Optional[List[Dict[str, Any]]] = None
    _plugins_fetched_at: float = 0.0
    _adapters_fetched_at: float = 0.0
    # 适配器安装探测缓存（安装/卸载后失效）
    _installed_cache: Dict[str, bool] = {}

    # ------------------------------------------------------------------
    # 状态与重启
    # ------------------------------------------------------------------

    def get_status(self) -> Dict[str, Any]:
        """汇总桥接全景状态。"""
        from channels.nonebot_bridge.runtime import get_nonebot_runtime

        channel = _get_channel()
        cfg = _read_channel_config()
        return {
            "enabled": bool(cfg.get("enabled", False)),
            "registered": channel is not None,
            "channel_status": channel.get_status_info() if channel else None,
            "install": get_nonebot_runtime().get_install_state(),
        }

    async def restart(self) -> Dict[str, Any]:
        """重启 worker 子进程。"""
        channel = _get_channel()
        if channel is None:
            return {"success": False, "error": "桥接频道未启用"}
        result = await channel.restart_worker()
        try:
            payload = json.loads(result)
        except (TypeError, ValueError):
            payload = {"success": False, "error": result}
        return payload

    # ------------------------------------------------------------------
    # 配置
    # ------------------------------------------------------------------

    def get_config(self) -> Dict[str, Any]:
        """读取桥接频道配置。"""
        cfg = _read_channel_config()
        defaults = {
            "enabled": False,
            "adapters": [],
            "plugins": [],
            "nonebot_env": {},
            "intercept_all": False,
            "bridge_ws_port": 8197,
            "worker_host": "127.0.0.1",
            "worker_port": 8198,
            "auto_restart": True,
        }
        defaults.update(cfg)
        return defaults

    async def save_config(self, patch: Dict[str, Any]) -> Dict[str, Any]:
        """保存配置补丁（enabled 不经此路径），worker 相关变更自动热重启。"""
        cfg = _read_channel_config()
        patch = dict(patch)
        patch.pop("enabled", None)

        worker_keys = {"adapters", "plugins", "nonebot_env", "intercept_all", "worker_host", "worker_port"}
        needs_restart = any(k in patch and cfg.get(k) != v for k, v in patch.items() if k in worker_keys)

        cfg.update(patch)
        _write_channel_config(cfg)

        channel = _get_channel()
        if channel is not None:
            # reload_config 内部检测 worker 配置签名变化并触发热重启
            channel.reload_config()
        return {"success": True, "restarted": needs_restart and channel is not None}

    def update_worker_config(self, mutate: Any) -> Dict[str, Any]:
        """读-改-写桥接配置（mutate: (cfg) -> None），返回最新配置。"""
        cfg = _read_channel_config()
        mutate(cfg)
        _write_channel_config(cfg)
        channel = _get_channel()
        if channel is not None:
            channel.reload_config()
        return cfg

    # ------------------------------------------------------------------
    # 适配器
    # ------------------------------------------------------------------

    def list_adapters(self) -> List[Dict[str, Any]]:
        """内置 ∪ 注册表适配器列表（含安装状态与启用状态）。"""
        from channels.nonebot_bridge.config import KNOWN_ADAPTERS

        cfg = _read_channel_config()
        enabled_keys = set(cfg.get("adapters") or [])

        entries: List[Dict[str, Any]] = []
        modules: List[str] = []
        for key, info in KNOWN_ADAPTERS.items():
            entries.append({
                "key": key,
                "label": info["label"],
                "package": info["package"],
                "module": info["import"],
                "builtin": True,
                "version": "",
                "setup": self._serialize_setup(info.get("setup")),
            })
            modules.append(info["import"])

        for item in self._load_adapters_snapshot():
            module_name = str(item.get("module_name", "") or "")
            if not module_name or any(e["module"] == module_name for e in entries):
                continue
            entries.append({
                "key": module_name.rsplit(".", 1)[-1],
                "label": str(item.get("name", "") or module_name),
                "package": str(item.get("project_link", "") or ""),
                "module": module_name,
                "builtin": False,
                "version": str(item.get("version", "") or ""),
                "setup": {
                    "difficulty": "medium",
                    "env_keys": [],
                    "notes": "社区适配器：安装后由通用协议转换兜底接入。",
                    "docs": str(item.get("homepage", "") or ""),
                },
            })
            modules.append(module_name)

        installed = self._probe_installed(modules)
        for entry in entries:
            entry["installed"] = installed.get(entry["module"], False)
            entry["enabled"] = entry["module"] in enabled_keys or entry["key"] in enabled_keys

        return entries

    async def install_adapter(self, key: str, enable: bool = True) -> Dict[str, Any]:
        """安装适配器包到 worker venv（可选同时加入启用列表）。"""
        from channels.nonebot_bridge.runtime import get_nonebot_runtime

        entry = self._find_adapter_entry(key)
        if entry is None:
            return {"success": False, "error": f"未知适配器: {key}"}
        package = entry["package"]
        if not package:
            return {"success": False, "error": "该适配器缺少包名信息"}

        runtime = get_nonebot_runtime()
        if not runtime.is_venv_ready():
            try:
                await runtime.ensure_venv()
            except Exception as exc:  # noqa: BLE001 - venv 引导失败如实回传
                return {"success": False, "error": f"venv 引导失败: {exc}"}

        result = await runtime.install_packages([package])
        type(self)._installed_cache.clear()

        if result.get("success") and enable:
            self.update_worker_config(
                lambda cfg: self._append_unique(cfg, "adapters", key)
            )
        return result

    async def uninstall_adapter(self, key: str) -> Dict[str, Any]:
        """卸载适配器包并从启用列表移除。"""
        from channels.nonebot_bridge.runtime import get_nonebot_runtime

        entry = self._find_adapter_entry(key)
        if entry is None:
            return {"success": False, "error": f"未知适配器: {key}"}

        self.update_worker_config(lambda cfg: self._remove_item(cfg, "adapters", key))
        runtime = get_nonebot_runtime()
        result = await runtime.uninstall_packages([entry["package"]])
        type(self)._installed_cache.clear()
        return result

    # ------------------------------------------------------------------
    # 插件
    # ------------------------------------------------------------------

    async def list_plugins(self) -> Dict[str, Any]:
        """worker 实时上报的已加载插件列表。"""
        channel = _get_channel()
        if channel is None:
            return {"success": False, "error": "桥接频道未启用"}
        snapshot = await channel.fetch_worker_status()
        return {"success": True, "plugins": snapshot.get("plugins", [])}

    async def install_plugin(self, module_name: str) -> Dict[str, Any]:
        """从商店安装插件：装包 → 加入 plugins 配置 → 热重启生效。"""
        store_entry = await self._find_store_plugin(module_name)
        if store_entry is None:
            return {
                "success": False,
                "error": f"商店中未找到插件 {module_name}，可先用 nonebot_store_search 检索",
            }
        project = str(store_entry.get("project_link", "") or "")
        if not project:
            return {"success": False, "error": "该插件缺少 PyPI 包名"}

        from channels.nonebot_bridge.runtime import get_nonebot_runtime

        runtime = get_nonebot_runtime()
        if not runtime.is_venv_ready():
            try:
                await runtime.ensure_venv()
            except Exception as exc:  # noqa: BLE001
                return {"success": False, "error": f"venv 引导失败: {exc}"}

        result = await runtime.install_packages([project])
        if not result.get("success"):
            return result

        self.update_worker_config(
            lambda cfg: self._append_unique(cfg, "plugins", module_name)
        )
        return {
            "success": True,
            "module": module_name,
            "package": project,
            "restarted": True,
            "install": result,
        }

    async def uninstall_plugin(self, module_name: str) -> Dict[str, Any]:
        """卸载插件：移出 plugins 配置 → 卸载包 → 热重启生效。"""
        from channels.nonebot_bridge.runtime import get_nonebot_runtime

        store_entry = await self._find_store_plugin(module_name)
        project = str((store_entry or {}).get("project_link", "") or "")

        self.update_worker_config(
            lambda cfg: self._remove_item(cfg, "plugins", module_name)
        )
        result: Dict[str, Any] = {"success": True, "module": module_name, "restarted": True}
        if project:
            uninstall = await get_nonebot_runtime().uninstall_packages([project])
            type(self)._installed_cache.clear()
            result["uninstall"] = uninstall
        return result

    # ------------------------------------------------------------------
    # 商店（registry.nonebot.dev 代理）
    # ------------------------------------------------------------------

    async def fetch_store_plugins(self, force: bool = False) -> List[Dict[str, Any]]:
        """拉取插件商店全量列表（TTL 缓存 + 磁盘快照兜底）。"""
        now = time.time()
        if not force and self._plugins_cache and now - self._plugins_fetched_at < _STORE_TTL:
            return self._plugins_cache
        data = await self._fetch_json(_PLUGINS_JSON)
        if data is not None:
            type(self)._plugins_cache = data
            type(self)._plugins_fetched_at = now
            self._save_snapshot("plugins.json", data)
            return data
        if self._plugins_cache:
            return self._plugins_cache
        return self._load_snapshot("plugins.json")

    async def fetch_store_adapters(self, force: bool = False) -> List[Dict[str, Any]]:
        """拉取适配器注册表列表（TTL 缓存 + 磁盘快照兜底）。"""
        now = time.time()
        if not force and self._adapters_cache and now - self._adapters_fetched_at < _STORE_TTL:
            return self._adapters_cache
        data = await self._fetch_json(_ADAPTERS_JSON)
        if data is not None:
            type(self)._adapters_cache = data
            type(self)._adapters_fetched_at = now
            self._save_snapshot("adapters.json", data)
            return data
        if self._adapters_cache:
            return self._adapters_cache
        return self._load_snapshot("adapters.json")

    async def search_store_plugins(self, keyword: str, limit: int = 8) -> List[Dict[str, Any]]:
        """按关键词搜索商店插件（名称/描述/模块/作者/标签）。"""
        plugins = await self.fetch_store_plugins()
        keyword = keyword.strip().lower()
        if not keyword:
            return plugins[:limit]

        def _score(item: Dict[str, Any]) -> int:
            module = str(item.get("module_name", "") or "").lower()
            name = str(item.get("name", "") or "").lower()
            desc = str(item.get("desc", "") or "").lower()
            author = str(item.get("author", "") or "").lower()
            tags = " ".join(
                str(t.get("label", "")).lower() for t in item.get("tags") or [] if isinstance(t, dict)
            )
            blob = f"{module} {name} {desc} {author} {tags}"
            if module == keyword or name == keyword:
                return 0
            if module.startswith(keyword) or keyword in name:
                return 1
            if keyword in blob:
                return 2
            return 3

        scored = sorted(
            ((p, _score(p)) for p in plugins if _score(p) < 3),
            key=lambda pair: pair[1],
        )
        return [p for p, _ in scored[:limit]]

    # ------------------------------------------------------------------
    # 日志与命令
    # ------------------------------------------------------------------

    def tail_logs(self, count: int = 200) -> List[str]:
        """读取 worker 日志环尾部。"""
        from channels.nonebot_bridge.runtime import get_nonebot_runtime

        return get_nonebot_runtime().tail_logs(count)

    async def run_command(self, command: str, bot_id: str = "", adapter: str = "") -> Dict[str, Any]:
        """经 worker 合成事件触发插件命令。"""
        channel = _get_channel()
        if channel is None:
            return {"ok": False, "error": "桥接频道未启用"}
        return await channel.run_worker_command(command, bot_id=bot_id, adapter=adapter)

    # ------------------------------------------------------------------
    # 内部实现
    # ------------------------------------------------------------------

    @staticmethod
    def _serialize_setup(setup: Any) -> Dict[str, Any]:
        """把 AdapterSetup 元数据序列化为 API 友好结构。"""
        if setup is None:
            return {"difficulty": "medium", "env_keys": [], "notes": "", "docs": ""}
        return {
            "difficulty": setup.difficulty,
            "env_keys": [
                {
                    "key": env.key,
                    "label": env.label,
                    "secret": env.secret,
                    "json_mode": env.json_mode,
                    "placeholder": env.placeholder,
                }
                for env in setup.env_keys
            ],
            "notes": setup.notes,
            "docs": setup.docs,
        }

    def _find_adapter_entry(self, key: str) -> Optional[Dict[str, Any]]:
        """按 key/module 定位适配器条目（内置优先，注册表兜底）。"""
        from channels.nonebot_bridge.config import KNOWN_ADAPTERS

        info = KNOWN_ADAPTERS.get(key)
        if info is not None:
            return {"key": key, "label": info["label"], "package": info["package"],
                    "module": info["import"]}
        for item in self._load_adapters_snapshot():
            module_name = str(item.get("module_name", "") or "")
            if module_name == key or module_name.rsplit(".", 1)[-1] == key:
                return {"key": module_name.rsplit(".", 1)[-1],
                        "label": str(item.get("name", "") or module_name),
                        "package": str(item.get("project_link", "") or ""),
                        "module": module_name}
        return None

    def _probe_installed(self, modules: List[str]) -> Dict[str, bool]:
        """在 worker venv 中批量探测模块是否已安装。"""
        from channels.nonebot_bridge.runtime import get_nonebot_runtime, venv_python

        runtime = get_nonebot_runtime()
        result = {module: False for module in modules}
        if not runtime.is_venv_ready():
            return result

        pending = [m for m in modules if m not in type(self)._installed_cache]
        if pending:
            import subprocess

            try:
                proc = subprocess.run(
                    [str(venv_python()), "-c", _PROBE_SCRIPT, json.dumps(pending)],
                    capture_output=True, text=True, timeout=_PROBE_TIMEOUT,
                )
                if proc.returncode == 0:
                    for line in reversed(proc.stdout.strip().splitlines()):
                        try:
                            data = json.loads(line)
                        except ValueError:
                            continue
                        if isinstance(data, dict):
                            type(self)._installed_cache.update(
                                {k: bool(v) for k, v in data.items()}
                            )
                            break
            except (OSError, subprocess.TimeoutExpired) as exc:
                log(f"NoneBot 适配器安装探测失败: {exc}", "WARNING")

        for module in modules:
            if module in type(self)._installed_cache:
                result[module] = type(self)._installed_cache[module]
        return result

    async def _find_store_plugin(self, module_name: str) -> Optional[Dict[str, Any]]:
        """在商店中精确定位插件（module_name 精确匹配）。"""
        plugins = await self.fetch_store_plugins()
        for item in plugins:
            if str(item.get("module_name", "") or "") == module_name:
                return item
        return None

    @staticmethod
    async def _fetch_json(url: str) -> Optional[List[Dict[str, Any]]]:
        """拉取注册表 JSON（失败返回 None，走缓存/快照兜底）。"""
        try:
            timeout = aiohttp.ClientTimeout(total=_FETCH_TIMEOUT)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url) as resp:
                    if resp.status != 200:
                        log(f"NoneBot 注册表拉取失败: {url} -> HTTP {resp.status}", "WARNING")
                        return None
                    data = await resp.json(content_type=None)
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as exc:
            log(f"NoneBot 注册表拉取异常: {url} - {exc}", "WARNING")
            return None
        return data if isinstance(data, list) else None

    def _load_adapters_snapshot(self) -> List[Dict[str, Any]]:
        """读取适配器注册表快照（缓存 → 磁盘 → 空）。"""
        if self._adapters_cache:
            return self._adapters_cache
        return self._load_snapshot("adapters.json")

    @staticmethod
    def _snapshot_dir() -> Path:
        from core.path import ConfigPaths

        path = Path(ConfigPaths.NONEBOT_DIR)
        path.mkdir(parents=True, exist_ok=True)
        return path

    @classmethod
    def _save_snapshot(cls, name: str, data: List[Dict[str, Any]]) -> None:
        try:
            target = cls._snapshot_dir() / name
            target.write_text(json.dumps(data, ensure_ascii=False), "utf-8")
        except OSError as exc:
            log(f"NoneBot 注册表快照写入失败: {exc}", "DEBUG")

    @classmethod
    def _load_snapshot(cls, name: str) -> List[Dict[str, Any]]:
        try:
            target = cls._snapshot_dir() / name
            if not target.exists():
                return []
            data = json.loads(target.read_text("utf-8"))
            return data if isinstance(data, list) else []
        except (OSError, ValueError):
            return []

    @staticmethod
    def _append_unique(cfg: Dict[str, Any], key: str, item: str) -> None:
        """向配置列表追加元素（去重保序）。"""
        values = list(cfg.get(key) or [])
        if item not in values:
            values.append(item)
        cfg[key] = values

    @staticmethod
    def _remove_item(cfg: Dict[str, Any], key: str, item: str) -> None:
        """从配置列表移除元素。"""
        values = list(cfg.get(key) or [])
        cfg[key] = [v for v in values if v != item]
