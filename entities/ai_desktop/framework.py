"""AI 桌面模块框架 — 上下文组件的装饰器注册表与统一管线。

新增组件只需在 ``modules/`` 下新建文件，定义 :class:`DesktopModule` 子类并加
``@desktop_module`` 装饰器，即自动完成三件事：

1. 被 ``modules/__init__.py`` 扫描导入并实例化注册；
2. 组件配置项自动加 ``ai_desktop_<key>_`` 前缀注册进 ``entity/ai_desktop`` 配置组
   （配置中心 / 实体详情配置 tab / AI 配置工具自动可见）；
3. 纳入 ``context.py`` 的注入管线（后台定时刷新 + 每轮 render 组装）。

Model Experience:
    模型看到什么 —— 各启用组件 render() 的文本经 ai_desktop context_provider
        拼成一个 ``[桌面环境]`` 块注入 volatile 层尾部；
    token 影响 —— 每轮增量注入，量级为几十至两百 token（天气行最长）；
    缓存影响 —— 走 volatile 尾部动态区，不触碰 stable/conversation 前缀缓存。
"""

from __future__ import annotations

import time
from typing import Any, ClassVar, Dict, List, Optional

from core.config import ConfigManager
from core.log import log

_LOG_TAG = "AI桌面"

_MODULES: Dict[str, "DesktopModule"] = {}


class DesktopModule:
    """桌面上下文组件基类。

    子类声明类属性即可接入框架；两类工作模式：

    - 即时型（``refresh_interval = 0``）：:meth:`render` 每次直接计算
      （要求零 I/O、毫秒级，如日期时间）；
    - 轮询型（``refresh_interval > 0``）：框架后台调度 :meth:`refresh`
      采集数据（允许网络 I/O），:meth:`render` 只读缓存快照。
    """

    key: ClassVar[str] = ""
    """组件全局唯一标识（英文，作为配置键/工具参数/API 路径的一部分）。"""
    display_name: ClassVar[str] = ""
    """展示名（面板/工具输出使用）。"""
    description: ClassVar[str] = ""
    """组件功能描述。"""
    priority: ClassVar[int] = 50
    """注入块内排序权重（越小越靠前）。"""
    default_enabled: ClassVar[bool] = True
    """未配置时的默认启用状态。"""
    refresh_interval: ClassVar[float] = 0.0
    """后台刷新间隔（秒）；0 表示即时型组件。"""
    config_schema: ClassVar[Dict[str, Dict[str, Any]]] = {}
    """组件配置项声明（键不含前缀，注册时自动加 ``ai_desktop_<key>_`` 前缀）。

    值格式与 ``core.config.register_configs`` 的 ConfigItem 一致：
    ``{name: {"description": ..., "default": ..., "unit": ..., "min": ..., ...}}``。
    """

    def __init__(self) -> None:
        self.last_refresh: float = 0.0
        """上次成功刷新时间戳（轮询型用）。"""
        self.last_error: str = ""
        """最近一次刷新/渲染错误（面板与工具展示用）。"""

    # ---- 配置 ----

    @classmethod
    def full_config_key(cls, name: str) -> str:
        """组件配置项的全局键（加实体前缀）。"""
        return f"ai_desktop_{cls.key}_{name}"

    @property
    def enabled_key(self) -> str:
        """组件启用开关的全局配置键。"""
        return f"ai_desktop_{self.key}_enabled"

    def is_enabled(self) -> bool:
        """组件当前是否启用（热读取，配置变更即时生效）。"""
        value = ConfigManager.get(self.enabled_key, self.default_enabled)
        if isinstance(value, str):
            return value.strip().lower() in ("1", "true", "yes", "on")
        return bool(value)

    def get_config(self, name: str, default: Any = None) -> Any:
        """读取组件配置项（未设置时回落 schema 声明的 default，再回落入参）。"""
        item = self.config_schema.get(name, {})
        fallback = item.get("default", default)
        return ConfigManager.get(self.full_config_key(name), fallback)

    # ---- 生命周期 ----

    async def refresh(self) -> None:
        """后台采集刷新（轮询型组件实现，允许 I/O）。

        实现需自行缓存结果供 :meth:`render` 读取，并在失败时记
        ``self.last_error``（异常向外抛出由框架兜底亦可）。
        """

    def render(self) -> Optional[str]:
        """返回注入文本（零 I/O）；None/空串表示本轮不注入。"""
        return None

    def detail(self) -> Dict[str, Any]:
        """组件当前状态的结构化详情（零 I/O，面板展示用）。

        子类按需提供字段（如天气的温度/湿度、时间的节日倒计时），
        面板按组件 key 特化渲染，未知组件回落到 current_content 文本。
        """
        return {}

    def due(self, now: float) -> bool:
        """是否到达下次刷新时间。"""
        return self.refresh_interval > 0 and (
            now - self.last_refresh >= self.refresh_interval
        )

    # ---- 序列化（Web API / AI 工具共用） ----

    def describe(self) -> Dict[str, Any]:
        """组件状态与配置的可序列化描述。

        配置项类型口径与统一配置架构一致（ConfigRegistry 的 type_name：
        boolean/integer/string/...），面板与配置中心同一词汇。
        """
        from core.config import ConfigRegistry

        configs: List[Dict[str, Any]] = []
        for name, item in self.config_schema.items():
            default = item.get("default")
            full_key = self.full_config_key(name)
            meta = ConfigRegistry.get_item(full_key)
            configs.append({
                "key": full_key,
                "name": name,
                "description": item.get("description", ""),
                "value": ConfigManager.get(full_key, default),
                "default": default,
                "value_type": meta.type_name if meta is not None
                else type(default).__name__,
                "unit": item.get("unit", ""),
                "min": item.get("min"),
                "max": item.get("max"),
                "advanced": bool(item.get("advanced", False)),
            })
        return {
            "key": self.key,
            "display_name": self.display_name,
            "description": self.description,
            "priority": self.priority,
            "enabled": self.is_enabled(),
            "default_enabled": self.default_enabled,
            "refresh_interval": self.refresh_interval,
            "last_refresh": self.last_refresh or None,
            "last_error": self.last_error,
            "configs": configs,
            "current_content": self._safe_render(),
            "detail": self._safe_detail(),
        }

    def _safe_render(self) -> Optional[str]:
        """render 的容错封装（面板查询不因单组件异常而失败）。"""
        if not self.is_enabled():
            return None
        try:
            return self.render()
        except Exception as exc:
            return f"（渲染异常: {exc}）"

    def _safe_detail(self) -> Dict[str, Any]:
        """detail 的容错封装。"""
        try:
            return self.detail()
        except Exception as exc:
            log(f"组件 {self.key} 详情异常: {exc}", "DEBUG", tag=_LOG_TAG)
            return {}


def desktop_module(cls: type[DesktopModule]) -> type[DesktopModule]:
    """注册桌面组件（实例化入注册表；同名覆盖）。"""
    if not cls.key:
        raise ValueError(f"{cls.__name__} 缺少 key 声明")
    _MODULES[cls.key] = cls()
    return cls


def get_module(key: str) -> Optional[DesktopModule]:
    """按 key 取组件实例。"""
    return _MODULES.get(key)


def all_modules() -> List[DesktopModule]:
    """全部组件（按注入排序权重排序）。"""
    return sorted(_MODULES.values(), key=lambda m: m.priority)


def config_entries() -> Dict[str, Any]:
    """汇总全部组件的配置项（键已加前缀），供注册进 entity/ai_desktop 组。"""
    entries: Dict[str, Any] = {}
    for module in all_modules():
        entries[module.enabled_key] = {
            "description": f"启用「{module.display_name}」组件上下文注入",
            "default": module.default_enabled,
        }
        for name, item in module.config_schema.items():
            entries[module.full_config_key(name)] = dict(item)
    return entries


def render_context() -> str:
    """组装当前注入文本（遍历启用组件，按优先级排序拼接）。"""
    parts: List[str] = []
    for module in all_modules():
        if not module.is_enabled():
            continue
        try:
            text = module.render()
            if module.last_error and text:
                module.last_error = ""
        except Exception as exc:
            module.last_error = str(exc)
            log(f"组件 {module.key} 渲染异常: {exc}", "WARNING", tag=_LOG_TAG)
            continue
        if text:
            parts.append(text.strip())
    if not parts:
        return ""
    return "[桌面环境]\n" + "\n".join(parts)


async def refresh_due(now: Optional[float] = None) -> None:
    """刷新所有到期的轮询型组件（单组件异常不影响其他组件）。"""
    current = now if now is not None else time.time()
    for module in all_modules():
        if not module.is_enabled() or not module.due(current):
            continue
        try:
            await module.refresh()
            module.last_refresh = current
            module.last_error = ""
        except Exception as exc:
            module.last_refresh = current  # 失败也推进，按间隔退避重试
            module.last_error = str(exc)
            log(f"组件 {module.key} 刷新异常: {exc}", "WARNING", tag=_LOG_TAG)


async def force_refresh(key: str) -> bool:
    """立即刷新指定组件（忽略间隔），返回是否成功。"""
    module = _MODULES.get(key)
    if module is None or module.refresh_interval <= 0:
        return False
    try:
        await module.refresh()
        module.last_refresh = time.time()
        module.last_error = ""
        return True
    except Exception as exc:
        module.last_error = str(exc)
        log(f"组件 {key} 手动刷新异常: {exc}", "WARNING", tag=_LOG_TAG)
        return False


def handle_config_changed(key: str, _value: Any) -> None:
    """配置变更钩子（ConfigManager 监听器）：命中组件配置键时立即使其到期。

    轮询型组件的刷新计时清零后，后台调度循环在下一拍（秒级）即重新采集，
    保证"改配置 → 上下文即时更新"，不再等待完整刷新间隔。
    """
    prefix = "ai_desktop_"
    if not key.startswith(prefix):
        return
    for module in _MODULES.values():
        if key.startswith(f"{prefix}{module.key}_"):
            module.last_refresh = 0.0
            return
