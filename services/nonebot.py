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
import re
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
_FETCH_RETRY_INTERVAL = 60.0
_PROBE_TIMEOUT = 30.0
_SECRET_ENV_RE = re.compile(r"token|secret|password|key", re.I)

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
    # 注册表最近一次拉取尝试（含失败，驱动 ensure 的退避）
    _adapters_attempt_at: float = 0.0
    # 适配器安装探测缓存（安装/卸载后失效）
    _installed_cache: Dict[str, bool] = {}

    # ------------------------------------------------------------------
    # 状态与重启
    # ------------------------------------------------------------------

    async def get_status(self) -> Dict[str, Any]:
        """汇总桥接全景状态（含环境概要；uv 版本探测走线程不阻塞循环）。"""
        from channels.nonebot_bridge.runtime import get_nonebot_runtime

        channel = _get_channel()
        cfg = _read_channel_config()
        runtime = get_nonebot_runtime()
        uv_version = await asyncio.to_thread(runtime.get_uv_version)
        return {
            "enabled": bool(cfg.get("enabled", False)),
            "registered": channel is not None,
            "channel_status": channel.get_status_info() if channel else None,
            "install": runtime.get_install_state(),
            "env": {
                "venv_ready": runtime.is_venv_ready(),
                "uv": uv_version,
                "uv_found": runtime._resolve_uv() is not None,  # noqa: SLF001 - 同域内部探测
            },
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

    async def start_worker(self) -> Dict[str, Any]:
        """启动 worker 子进程（频道须已启用）。"""
        channel = _get_channel()
        if channel is None:
            return {"success": False, "error": "桥接频道未启用，请先在通道页启用"}
        from channels.nonebot_bridge.runtime import get_nonebot_runtime

        if get_nonebot_runtime().is_process_alive():
            return {"success": True, "message": "worker 已在运行"}
        result = await channel.restart_worker()
        try:
            payload = json.loads(result)
        except (TypeError, ValueError):
            payload = {"success": False, "error": result}
        return payload

    async def stop_worker(self) -> Dict[str, Any]:
        """停止 worker 子进程（不停止频道本身，可随时再启动）。"""
        from channels.nonebot_bridge.runtime import get_nonebot_runtime

        await get_nonebot_runtime().stop_worker()
        return {"success": True, "message": "worker 已停止"}

    async def _restart_worker_if_alive(self) -> bool:
        """worker 正在运行则重启（环境/包变更后让新代码立即生效）。"""
        from channels.nonebot_bridge.runtime import get_nonebot_runtime

        channel = _get_channel()
        if channel is None or not get_nonebot_runtime().is_process_alive():
            return False
        await channel.restart_worker()
        return True

    async def send_to_platform(
        self,
        chat_id: str,
        text: str,
        channel_type: str = "private",
        bot_id: str = "",
        adapter: str = "",
    ) -> Dict[str, Any]:
        """经桥接频道向平台目标发送消息（AI 工具 / 调试共用）。"""
        channel = _get_channel()
        if channel is None:
            return {"success": False, "error": "桥接频道未启用"}
        result = await channel.send_text(
            chat_id, text, channel_type=channel_type, bot_id=bot_id, adapter=adapter
        )
        try:
            payload = json.loads(result)
        except (TypeError, ValueError):
            payload = {"success": False, "error": str(result)}
        return payload

    async def send_media_to_platform(
        self,
        chat_id: str,
        kind: str,
        source: str,
        caption: str = "",
        name: str = "",
        channel_type: str = "private",
        bot_id: str = "",
        adapter: str = "",
    ) -> Dict[str, Any]:
        """经桥接频道向平台目标发送媒体（image/voice/video/file）。"""
        channel = _get_channel()
        if channel is None:
            return {"success": False, "error": "桥接频道未启用"}
        result = await channel.send_media(
            chat_id, kind, source, caption=caption, name=name,
            channel_type=channel_type, bot_id=bot_id, adapter=adapter,
        )
        try:
            payload = json.loads(result)
        except (TypeError, ValueError):
            payload = {"success": False, "error": str(result)}
        return payload

    # ------------------------------------------------------------------
    # 环境管理（uv / venv / 包）
    # ------------------------------------------------------------------

    async def bootstrap_env(self) -> Dict[str, Any]:
        """显式引导 worker venv（不依赖频道启用）。"""
        from channels.nonebot_bridge.runtime import get_nonebot_runtime

        runtime = get_nonebot_runtime()
        if runtime.is_venv_ready():
            return {"success": True, "message": "环境已就绪", "already": True}
        try:
            await runtime.ensure_venv(proxy=self._pip_proxy())
        except Exception as exc:  # noqa: BLE001 - 引导失败如实回传
            return {"success": False, "error": f"环境引导失败: {exc}"}
        return {"success": True, "message": "环境初始化完成"}

    async def get_env_status(self) -> Dict[str, Any]:
        """环境详情：uv / Python 版本、基线包、venv 就绪态、安装进度。"""
        from channels.nonebot_bridge import runtime as nb_runtime
        from channels.nonebot_bridge.runtime import get_nonebot_runtime

        rt = get_nonebot_runtime()
        return {
            "venv_ready": rt.is_venv_ready(),
            "uv_found": rt._resolve_uv() is not None,  # noqa: SLF001 - 同域内部探测
            "uv_version": await asyncio.to_thread(rt.get_uv_version),
            "python_version": await rt.get_python_version(),
            "baseline": list(nb_runtime._BASELINE_PACKAGES),  # noqa: SLF001 - 模块常量读取
            "venv_path": str(nb_runtime.venv_dir()),
            "runtime_dir": str(nb_runtime.runtime_dir()),
            "install": rt.get_install_state(),
        }

    async def list_packages(self) -> Dict[str, Any]:
        """列出 worker venv 已安装包（名称 + 版本）。"""
        from channels.nonebot_bridge.runtime import get_nonebot_runtime

        packages = await get_nonebot_runtime().list_installed_packages()
        return {"success": True, "count": len(packages), "packages": packages}

    async def upgrade_env(self, packages: Optional[List[str]] = None) -> Dict[str, Any]:
        """升级包（缺省升级 NoneBot 基线，即 nonebot2 本体更新）。"""
        from channels.nonebot_bridge import runtime as nb_runtime
        from channels.nonebot_bridge.runtime import get_nonebot_runtime

        rt = get_nonebot_runtime()
        if not rt.is_venv_ready():
            try:
                await rt.ensure_venv(proxy=self._pip_proxy())
            except Exception as exc:  # noqa: BLE001
                return {"success": False, "error": f"venv 引导失败: {exc}"}
        targets = (
            list(packages) if packages
            else list(nb_runtime._BASELINE_PACKAGES)  # noqa: SLF001 - 模块常量读取
        )
        result = await rt.upgrade_packages(targets, index_url=self._pip_index(), proxy=self._pip_proxy())
        if result.get("success"):
            # 包已更新：重启 worker 让新代码生效（未运行则跳过，下次启动自然生效）
            result["restarted"] = await self._restart_worker_if_alive()
        return result

    async def rebuild_env(self) -> Dict[str, Any]:
        """删除并重建 worker venv（运行中先停止，重建后自动恢复）。"""
        from channels.nonebot_bridge.runtime import get_nonebot_runtime

        runtime = get_nonebot_runtime()
        was_alive = runtime.is_process_alive()
        channel = _get_channel()
        if was_alive:
            await runtime.stop_worker()
        try:
            await runtime.rebuild_venv(proxy=self._pip_proxy())
        except Exception as exc:  # noqa: BLE001
            return {"success": False, "error": f"重建失败: {exc}"}
        if was_alive and channel is not None:
            await channel.restart_worker()
        message = "环境已重建" + ("，worker 恢复中" if was_alive else "")
        return {"success": True, "message": message}

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
            "pip_index_url": "",
            "pip_proxy": "",
            "package_specs": {},
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

    # 顶层配置项的类型规格（set_config_value 校验/强转用）
    _TOP_KEY_SPEC: Dict[str, type] = {
        "adapters": list,
        "plugins": list,
        "intercept_all": bool,
        "auto_restart": bool,
        "bridge_ws_port": int,
        "worker_port": int,
        "worker_host": str,
        "pip_index_url": str,
        "pip_proxy": str,
    }

    def get_config_masked(self) -> Dict[str, Any]:
        """读取配置（敏感环境变量值遮盖，供 AI / 展示使用）。"""
        cfg = self.get_config()
        env = dict(cfg.get("nonebot_env") or {})
        for key in list(env.keys()):
            if _SECRET_ENV_RE.search(key):
                env[key] = "********" if str(env[key]) else ""
        cfg["nonebot_env"] = env
        return cfg

    def set_config_value(self, key: str, value: Any) -> Dict[str, Any]:
        """按 key 原子写配置项（支持 ``nonebot_env.<ENV_KEY>`` 点路径，空值删除）。

        顶层 key 按 _TOP_KEY_SPEC 校验并强转；worker 相关项变更触发热重启。
        """
        spec = type(self)._TOP_KEY_SPEC

        if key.startswith("nonebot_env."):
            env_key = key[len("nonebot_env."):]
            if not env_key:
                return {"success": False, "error": "环境变量名为空"}

            def _mutate_env(cfg: Dict[str, Any]) -> None:
                env = dict(cfg.get("nonebot_env") or {})
                if value is None or str(value) == "":
                    env.pop(env_key, None)
                else:
                    env[env_key] = str(value)
                cfg["nonebot_env"] = env

            cfg = self.update_worker_config(_mutate_env)
            return {"success": True, "key": key, "config": cfg}

        if key not in spec:
            return {
                "success": False,
                "error": f"未知配置项 '{key}'，支持: {sorted(spec)}, nonebot_env.<ENV_KEY>",
            }
        coerced = self._coerce_config_value(spec[key], value)
        if coerced is None:
            return {"success": False, "error": f"配置项 '{key}' 的值类型不合法: {value!r}"}

        def _mutate_top(cfg: Dict[str, Any]) -> None:
            cfg[key] = coerced

        cfg = self.update_worker_config(_mutate_top)
        return {"success": True, "key": key, "value": coerced, "config": cfg}

    @staticmethod
    def _coerce_config_value(expected: type, value: Any) -> Any:
        """按期望类型强转配置值（失败返回 None）。"""
        if expected is bool:
            if isinstance(value, bool):
                return value
            if isinstance(value, str) and value.lower() in ("true", "false"):
                return value.lower() == "true"
            return None
        if expected is int:
            try:
                return int(value)
            except (TypeError, ValueError):
                return None
        if expected is list:
            if isinstance(value, list):
                return [str(v) for v in value]
            if isinstance(value, str) and value.strip():
                return [part.strip() for part in value.split(",") if part.strip()]
            return []
        return str(value)

    # ------------------------------------------------------------------
    # 启用 / 停用（不装卸包）
    # ------------------------------------------------------------------

    def set_adapter_enabled(self, key: str, enabled: bool) -> Dict[str, Any]:
        """启用/停用适配器（仅调整加载列表，不动包）。"""
        def _mutate(cfg: Dict[str, Any]) -> None:
            adapters = [a for a in (cfg.get("adapters") or []) if a != key]
            if enabled:
                adapters.append(key)
            cfg["adapters"] = adapters

        cfg = self.update_worker_config(_mutate)
        return {"success": True, "key": key, "enabled": enabled,
                "adapters": cfg.get("adapters")}

    def set_plugin_enabled(self, module: str, enabled: bool) -> Dict[str, Any]:
        """启用/停用插件（仅调整加载列表，保留已安装包）。"""
        def _mutate(cfg: Dict[str, Any]) -> None:
            plugins = [p for p in (cfg.get("plugins") or []) if p != module]
            if enabled:
                plugins.append(module)
            cfg["plugins"] = plugins

        cfg = self.update_worker_config(_mutate)
        return {"success": True, "module": module, "enabled": enabled,
                "plugins": cfg.get("plugins")}

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

    def _pip_index(self) -> str:
        """读取自定义 PyPI 安装源（空 = 默认源）。"""
        return str(_read_channel_config().get("pip_index_url", "") or "")

    def _pip_proxy(self) -> str:
        """读取安装代理（空=继承系统；off/none=强制直连；其余=使用该代理）。"""
        return str(_read_channel_config().get("pip_proxy", "") or "").strip()

    def _record_package_spec(self, name_key: str, spec: str) -> None:
        """记录安装溯源（非默认 PyPI 源时，供卸载/重装推导分发名）。"""
        def _mutate(cfg: Dict[str, Any]) -> None:
            specs = dict(cfg.get("package_specs") or {})
            specs[name_key] = spec
            cfg["package_specs"] = specs

        self.update_worker_config(_mutate)

    def _pop_package_spec(self, name_key: str) -> str:
        """移除溯源记录并返回 spec（无记录返回空串）。"""
        cfg = _read_channel_config()
        spec = str((cfg.get("package_specs") or {}).get(name_key, "") or "")

        def _mutate(c: Dict[str, Any]) -> None:
            specs = dict(c.get("package_specs") or {})
            specs.pop(name_key, None)
            c["package_specs"] = specs

        self.update_worker_config(_mutate)
        return spec

    async def _install_with_source(
        self, spec: str, editable: bool, refresh: bool = False
    ) -> Dict[str, Any]:
        """按安装源执行安装（含 venv 引导与自定义索引）。"""
        from channels.nonebot_bridge.runtime import get_nonebot_runtime

        runtime = get_nonebot_runtime()
        proxy = self._pip_proxy()
        if not runtime.is_venv_ready():
            try:
                await runtime.ensure_venv(proxy=proxy)
            except Exception as exc:  # noqa: BLE001
                return {"success": False, "error": f"venv 引导失败: {exc}"}
        return await runtime.install_packages(
            [spec], index_url=self._pip_index(), editable=editable, proxy=proxy,
            refresh=refresh,
        )

    async def _localize_git_source(self, spec: str) -> str:
        """git 源 → 本地仓库目录检出，返回本地路径（非 git 源原样返回）。

        检出目录 <数据目录>/nonebot/repos/<仓库名>（随数据目录被 git 忽略），
        用户可直接浏览/修改源码；pull --ff-only 保证更新不覆盖本地修改。
        """
        from channels.nonebot_bridge.runtime import get_nonebot_runtime, parse_git_spec

        if parse_git_spec(spec) is None:
            return spec
        result = await get_nonebot_runtime().sync_git_source(spec, proxy=self._pip_proxy())
        if not result.get("success"):
            raise RuntimeError(f"git 源同步失败: {result.get('error')}")
        return str(result["path"])

    async def resync_sources(self) -> Dict[str, Any]:
        """按溯源记录更新全部 git 源安装项：拉取最新代码 + 强制重装。

        可编辑安装项跳过重装（本就直连源码目录，改代码即时生效）。
        """
        specs = dict(_read_channel_config().get("package_specs") or {})
        results: List[Dict[str, Any]] = []
        updated = 0
        for name_key, spec in specs.items():
            from channels.nonebot_bridge.runtime import parse_git_spec

            if parse_git_spec(spec) is None:
                continue
            try:
                local = await self._localize_git_source(spec)
                # refresh=True：强制重取重装（否则 uv 判定已安装跳过）
                install = await self._install_with_source(local, editable=False, refresh=True)
                install.update({"source": name_key, "path": local})
                results.append(install)
                if install.get("success"):
                    updated += 1
            except Exception as exc:  # noqa: BLE001 - 单项失败不中断其余
                results.append({"success": False, "source": name_key, "error": str(exc)})
        restarted = False
        if updated:
            # 源码已更新并重装：重启 worker 加载新代码
            restarted = await self._restart_worker_if_alive()
        return {
            "success": all(r.get("success") for r in results) if results else True,
            "total": len(results),
            "updated": updated,
            "restarted": restarted,
            "results": results,
        }

    def get_sources_status(self) -> Dict[str, Any]:
        """git/本地源安装项清单（溯源记录 + 本地检出路径存在性）。"""
        from channels.nonebot_bridge.runtime import sources_dir

        specs = dict(_read_channel_config().get("package_specs") or {})
        items = []
        for name_key, spec in sorted(specs.items()):
            repo_name = spec.rstrip("/").rsplit("/", 1)[-1]
            local = sources_dir() / (repo_name[:-4] if repo_name.endswith(".git") else repo_name)
            items.append({
                "key": name_key,
                "spec": spec,
                "kind": "git" if spec.startswith(("git+", "https://", "http://")) else "path",
                "repo_path": str(local),
                "repo_exists": local.exists(),
            })
        return {"sources_dir": str(sources_dir()), "items": items}

    async def _uninstall_with_candidates(self, candidates: List[str]) -> Dict[str, Any]:
        """按候选分发名卸载（git 仓库名 ≠ dist 名的兜底）。

        先与已装包清单做规范化名称匹配（连字符/下划线等价），只对真实
        存在的候选执行卸载 —— uv 对不存在的包名会静默成功（exit 0），
        不能用"命令成功"作为候选命中的判据。
        """
        from channels.nonebot_bridge.runtime import get_nonebot_runtime

        runtime = get_nonebot_runtime()

        def _norm(name: str) -> str:
            return name.strip().lower().replace("_", "-")

        installed = await runtime.list_installed_packages()
        norm_map = {_norm(p["name"]): p["name"] for p in installed}
        for candidate in dict.fromkeys(c for c in candidates if c):
            target = norm_map.get(_norm(candidate))
            if target is None:
                continue
            result = await runtime.uninstall_packages([target])
            if result.get("success"):
                return result
        tried = "、".join(c for c in candidates if c)
        return {"success": False, "error": f"包未安装（候选：{tried}）"}


    async def install_adapter(
        self, key: str, enable: bool = True, source: str = ""
    ) -> Dict[str, Any]:
        """安装适配器包到 worker venv（可选同时加入启用列表）。

        source：空 = PyPI 包名（注册表）；否则为 git 源（git+URL / user/repo /
        https://x.git）或本地路径 —— 适配器源码仓库自己管理时使用。
        """
        from channels.nonebot_bridge.runtime import normalize_install_spec

        # 社区适配器条目来自注册表，冷缓存时先尽力加载（AI 直接安装场景）
        await self.ensure_adapters_loaded()
        entry = self._find_adapter_entry(key)
        if entry is None:
            return {"success": False, "error": f"未知适配器: {key}"}
        package = entry["package"]
        if not package and not source:
            return {"success": False, "error": "该适配器缺少包名信息"}

        spec = normalize_install_spec(source) if source.strip() else (package or "")
        try:
            install_spec = await self._localize_git_source(spec)
        except RuntimeError as exc:
            return {"success": False, "error": str(exc)}
        result = await self._install_with_source(install_spec, editable=False)
        type(self)._installed_cache.clear()

        if result.get("success"):
            if source.strip():
                self._record_package_spec(f"adapter:{key}", spec)
            if enable:
                self.update_worker_config(
                    lambda cfg: self._append_unique(cfg, "adapters", key)
                )
        return result

    async def uninstall_adapter(self, key: str) -> Dict[str, Any]:
        """卸载适配器包并从启用列表移除（分发名：溯源 spec > 注册表包名）。"""
        from channels.nonebot_bridge.runtime import (
            derive_package_name,
        )

        entry = self._find_adapter_entry(key)
        if entry is None:
            return {"success": False, "error": f"未知适配器: {key}"}

        self.update_worker_config(lambda cfg: self._remove_item(cfg, "adapters", key))
        spec = self._pop_package_spec(f"adapter:{key}")
        candidates = [derive_package_name(spec)] if spec else []
        candidates += [entry["package"], key.replace("_", "-")]
        result = await self._uninstall_with_candidates(candidates)
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

    async def install_plugin(
        self, module_name: str, source: str = "", editable: bool = False
    ) -> Dict[str, Any]:
        """安装插件：装包 → 加入 plugins 配置 → 热重启生效。

        source：空 = 商店 PyPI 包名；否则为 git 源（git+URL / user/repo /
        https://x.git）或本地路径。editable 仅对本地路径有意义（可编辑安装，
        仓库代码改动即时生效，适合自维护插件开发）。
        """
        from channels.nonebot_bridge.runtime import normalize_install_spec

        store_entry = await self._find_store_plugin(module_name) if not source.strip() else None
        project = str((store_entry or {}).get("project_link", "") or "")
        if not source.strip():
            if store_entry is None:
                return {
                    "success": False,
                    "error": f"商店中未找到插件 {module_name}，可先用 nonebot_store_search 检索",
                }
            if not project:
                return {"success": False, "error": "该插件缺少 PyPI 包名"}

        spec = normalize_install_spec(source) if source.strip() else project
        try:
            install_spec = await self._localize_git_source(spec)
        except RuntimeError as exc:
            return {"success": False, "error": str(exc)}
        result = await self._install_with_source(install_spec, editable=editable)
        if not result.get("success"):
            return result

        def _mutate(cfg: Dict[str, Any]) -> None:
            self._append_unique(cfg, "plugins", module_name)
            if source.strip():
                specs = dict(cfg.get("package_specs") or {})
                specs[module_name] = spec
                cfg["package_specs"] = specs

        self.update_worker_config(_mutate)
        return {
            "success": True,
            "module": module_name,
            "package": spec,
            "restarted": True,
            "install": result,
        }

    async def uninstall_plugin(self, module_name: str) -> Dict[str, Any]:
        """卸载插件：移出 plugins 配置 → 卸载包 → 热重启生效。

        分发名推导：溯源 spec（git/本地安装时记录）> 商店包名 > 模块名兜底。
        """
        from channels.nonebot_bridge.runtime import derive_package_name

        store_entry = await self._find_store_plugin(module_name)
        project = str((store_entry or {}).get("project_link", "") or "")
        spec = self._pop_package_spec(module_name)

        def _mutate(cfg: Dict[str, Any]) -> None:
            self._remove_item(cfg, "plugins", module_name)
            specs = dict(cfg.get("package_specs") or {})
            specs.pop(module_name, None)
            cfg["package_specs"] = specs

        self.update_worker_config(_mutate)
        result: Dict[str, Any] = {"success": True, "module": module_name, "restarted": True}
        candidates = [derive_package_name(spec)] if spec else []
        candidates += [project, module_name.replace("_", "-")]
        result["uninstall"] = await self._uninstall_with_candidates(candidates)
        type(self)._installed_cache.clear()
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
        type(self)._adapters_attempt_at = now
        data = await self._fetch_json(_ADAPTERS_JSON)
        if data is not None:
            type(self)._adapters_cache = data
            type(self)._adapters_fetched_at = now
            self._save_snapshot("adapters.json", data)
            return data
        if self._adapters_cache:
            return self._adapters_cache
        return self._load_snapshot("adapters.json")

    async def ensure_adapters_loaded(self, wait: float = 6.0) -> None:
        """确保适配器注册表已加载（供适配器列表/安装路径调用）。

        尽力而为：缓存新鲜直接返回；距上次尝试过近（含失败）按退避跳过；
        网络拉取限时 ``wait`` 秒，超时不中断后台刷新（asyncio.shield）。
        """
        now = time.time()
        if self._adapters_cache and now - self._adapters_fetched_at < _STORE_TTL:
            return
        if now - type(self)._adapters_attempt_at < _FETCH_RETRY_INTERVAL:
            return

        task = asyncio.create_task(self.fetch_store_adapters(force=True))
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=wait)
        except asyncio.TimeoutError:
            pass  # 超时放行：后台任务继续，成功后写缓存与快照
        except Exception:  # noqa: BLE001 - 拉取失败走快照/内置兜底
            task.cancel()

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
