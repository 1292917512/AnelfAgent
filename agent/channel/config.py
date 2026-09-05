"""频道配置统一接入 — schema 扫描注册 + 频道目录文件存储后端。

约定：每个频道目录的 ``config.py`` 暴露标准符号 ``CONFIG_MODEL``（ChannelConfig
子类，pydantic 模型即配置声明源），配置值存频道目录下的 ``channel_config.json``
（字段名无前缀，模块自持有）。本模块在 bootstrap 阶段（ConfigManager 初始化后、
频道启动前）扫描注册：

1. ``ChannelConfigStore`` 经 ``ConfigManager.register_store(<id>_, store)`` 接入——
   统一配置面（Web /config/meta、AI entity 组工具、``set_channel_config``）的读写
   自动路由到频道目录文件，app_config.json 不存频道键
2. 派生 ConfigItem 注册进 ConfigRegistry（组 ``adapter/<id>``，键 ``<id>_<field>``，
   仅注册频道子类自己声明的字段，ChannelConfig 基类通用字段不进配置面）
3. ConfigWatcher 监听频道配置文件：手工编辑等文件级变更 diff 后
   经 ``ConfigManager.notify_external`` 驱动变更监听热更；进程内写入由
   ConfigManager.set 直接通知监听（内容无 diff，watcher 不重复触发）
4. 频道实例化后由 BaseChannel 从 ConfigManager 物化配置并注册变更监听热更
"""

from __future__ import annotations

import importlib
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Type

from core.config import ConfigManager, ConfigStore, parse_env_value, register_model_configs
from core.log import log
from core.path import project_root

from .base import ChannelConfig

_CONFIG_MODEL_SYMBOL = "CONFIG_MODEL"
_MISSING = object()  # 值缺失哨兵（文件 diff 区分「不存在」与 None）


def channels_dir() -> Path:
    """频道目录（基于项目根的绝对路径，不依赖进程 CWD）。"""
    return Path(project_root()) / "channels"


def config_key(channel_id: str, field: str) -> str:
    """频道字段在统一配置面的配置键（<channel_id>_<field>）。"""
    return f"{channel_id}_{field}"


def set_channel_config(channel_id: str, **fields: Any) -> None:
    """写入频道配置并持久化（统一配置面入口；变更监听驱动频道内存态热更）。

    供频道内部代码（登录回填/直播开关等）使用，禁止直写 channel_config.json。
    """
    for field, value in fields.items():
        ConfigManager.set(config_key(channel_id, field), value)
    ConfigManager.save()


class ChannelConfigStore(ConfigStore):
    """频道目录配置文件存储（channels/<id>/channel_config.json，字段名无前缀）。

    读取优先级：``ANELF_<CHANNEL_ID>_<FIELD>`` 环境变量 > 文件值 > 调用方默认值。
    """

    def __init__(self, channel_id: str, path: Path) -> None:
        self._channel_id = channel_id
        self._prefix = f"{channel_id}_"
        self._path = path
        self._values: Dict[str, Any] = {}

    @property
    def path(self) -> Path:
        return self._path

    def _field(self, key: str) -> str:
        return key[len(self._prefix):] if key.startswith(self._prefix) else key

    def load(self) -> None:
        """从频道配置文件加载（缺失/损坏按空配置）。"""
        self._values = {}
        if not self._path.exists():
            return
        try:
            data = json.loads(self._path.read_text("utf-8"))
            if isinstance(data, dict):
                self._values = data
        except (json.JSONDecodeError, OSError) as exc:
            log(f"频道配置解析失败 ({self._path}): {exc}", "WARNING", tag="通道")

    def get(self, key: str, default: Any = None) -> Any:
        env_val = os.environ.get(f"ANELF_{key.upper()}")
        if env_val is not None:
            return parse_env_value(env_val)
        return self._values.get(self._field(key), default)

    def set(self, key: str, value: Any) -> None:
        self._values[self._field(key)] = value

    def has(self, key: str) -> bool:
        return f"ANELF_{key.upper()}" in os.environ or self._field(key) in self._values

    def save(self) -> None:
        """原子落盘频道配置文件。"""
        from core.file_utils import atomic_write_text

        self._path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(
            self._path, json.dumps(self._values, indent=2, ensure_ascii=False) + "\n",
        )

    def reload_notify(self) -> None:
        """文件级外部变更入口（ConfigWatcher 回调）：重读文件并 diff 上报变更监听。"""
        old = dict(self._values)
        self.load()
        changed = {
            f for f in set(old) | set(self._values)
            if old.get(f, _MISSING) != self._values.get(f, _MISSING)
        }
        for field in sorted(changed):
            ConfigManager.notify_external(
                self._prefix + field, self._values.get(field),
            )
        if changed:
            log(f"频道配置文件变更已热更: {self._channel_id}（{sorted(changed)}）",
                "DEBUG", tag="通道")


def _declared_fields(model_cls: Type[ChannelConfig]) -> Set[str]:
    """频道子类自己声明的字段名（沿 MRO 收集 __annotations__，到 ChannelConfig 为止）。"""
    declared: Set[str] = set()
    for klass in model_cls.__mro__:
        if klass is ChannelConfig:
            break
        declared.update(getattr(klass, "__annotations__", {}) or {})
    return declared


def load_channel_config_model(channel_id: str) -> Optional[Type[ChannelConfig]]:
    """导入频道 config 模块并返回其 CONFIG_MODEL（无 config.py 或导入失败返回 None）。

    优先按包路径导入（支持 config.py 内的相对导入）；包不可用时回退按文件路径加载。
    """
    config_file = channels_dir() / channel_id / "config.py"
    if not config_file.exists():
        return None
    try:
        mod = importlib.import_module(f"channels.{channel_id}.config")
    except Exception:
        try:
            import sys

            spec = importlib.util.spec_from_file_location(
                f"_channel_cfg_{channel_id}", config_file,
            )
            if spec is None or spec.loader is None:
                return None
            mod = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = mod
            spec.loader.exec_module(mod)
        except Exception as exc:
            log(f"频道配置模块加载失败: {channel_id} - {exc}", "WARNING", tag="通道")
            return None
    model = getattr(mod, _CONFIG_MODEL_SYMBOL, None)
    if not (isinstance(model, type) and issubclass(model, ChannelConfig)):
        log(f"频道 {channel_id} 的 config.py 缺少 {_CONFIG_MODEL_SYMBOL}（ChannelConfig 子类）",
            "WARNING", tag="通道")
        return None
    return model


def register_channel_schemas() -> List[str]:
    """扫描 channels/ 注册所有频道的配置存储与 schema，返回注册的频道 id 列表。

    幂等：重复调用仅刷新 schema 定义与重载配置文件，已有值不被默认值覆盖。
    """
    from .config_watcher import get_config_watcher

    root = channels_dir()
    registered: List[str] = []
    if not root.is_dir():
        return registered
    for item in sorted(root.iterdir()):
        if not item.is_dir() or item.name.startswith("_"):
            continue
        if not (item / "adapter.py").exists():
            continue
        model = load_channel_config_model(item.name)
        if model is None:
            continue
        store = ChannelConfigStore(item.name, item / "channel_config.json")
        ConfigManager.register_store(f"{item.name}_", store)
        register_model_configs(
            f"adapter/{item.name}", model,
            key_prefix=item.name, only_fields=_declared_fields(model),
        )
        get_config_watcher().watch(str(store.path), store.reload_notify)
        registered.append(item.name)
    return registered
