"""智能家居设备域框架 — 设备类型组件的装饰器注册表与统一管线。

新增设备域只需在 ``domains/`` 下新建文件，定义 :class:`DeviceDomain` 子类并加
``@device_domain`` 装饰器，即自动完成三件事：

1. 被 ``domains/__init__.py`` 扫描导入并实例化注册；
2. 域配置项自动加 ``smart_home_<key>_`` 前缀注册进 ``entity/smart_home`` 配置组
   （配置中心 / 实体详情配置 tab / AI 配置工具自动可见）；
3. 纳入 ``context.py`` 的注入管线（按 priority 排序逐域渲染）。

Model Experience:
    模型看到什么 —— 各启用域 render() 的文本聚合成一个 ``[智能家居]`` 块注入
        volatile 层尾部；
    token 影响 —— 每轮增量注入，量级取决于设备数（受各域 inject_limit 约束）；
    缓存影响 —— 走 volatile 尾部动态区，不触碰 stable/conversation 前缀缓存。
"""

from __future__ import annotations

from typing import Any, ClassVar, Dict, List, Optional, Tuple

from core.config import ConfigManager
from core.log import log
from core.tool_errors import ErrorCause

from .models import ActionSpec, DeviceState, SmartHomeCallError

_LOG_TAG = "智能家居"

_DOMAINS: Dict[str, "DeviceDomain"] = {}
_BY_HA_DOMAIN: Dict[str, "DeviceDomain"] = {}


class DeviceDomain:
    """设备域组件基类（一种设备类型的注入渲染 / 控制动作 / 配置声明）。

    子类声明类属性即可接入框架；设备状态快照由 manager 实时供给，
    :meth:`render` 要求零 I/O 直读快照。
    """

    key: ClassVar[str] = ""
    """域全局唯一标识（英文，作为配置键/工具参数/API 参数的一部分）。"""
    display_name: ClassVar[str] = ""
    """展示名（注入行前缀/面板/工具输出使用）。"""
    description: ClassVar[str] = ""
    """域功能描述。"""
    priority: ClassVar[int] = 50
    """注入块内排序权重（越小越靠前）。"""
    default_enabled: ClassVar[bool] = True
    """未配置时的默认启用状态。"""
    ha_domains: ClassVar[Tuple[str, ...]] = ()
    """匹配的平台设备域（entity_id 前缀）。"""
    config_schema: ClassVar[Dict[str, Dict[str, Any]]] = {
        "inject_limit": {
            "description": "上下文注入该域最多列出的设备台数（0 为不限制）",
            "default": 8,
            "min": 0,
            "max": 50,
            "unit": "台",
            "advanced": True,
        },
    }
    """域配置项声明（键不含前缀，注册时自动加 ``smart_home_<key>_`` 前缀）。

    值格式与 ``core.config.register_configs`` 的 ConfigItem 一致；
    子类追加配置项时应合并基类声明（``{**DeviceDomain.config_schema, ...}``）。
    """

    # ---- 配置 ----

    @classmethod
    def full_config_key(cls, name: str) -> str:
        """域配置项的全局键（加实体前缀）。"""
        return f"smart_home_{cls.key}_{name}"

    @property
    def enabled_key(self) -> str:
        """域启用开关的全局配置键。"""
        return f"smart_home_{self.key}_enabled"

    def is_enabled(self) -> bool:
        """域当前是否启用（热读取，配置变更即时生效）。"""
        value = ConfigManager.get(self.enabled_key, self.default_enabled)
        if isinstance(value, str):
            return value.strip().lower() in ("1", "true", "yes", "on")
        return bool(value)

    def get_config(self, name: str, default: Any = None) -> Any:
        """读取域配置项（未设置时回落 schema 声明的 default，再回落入参）。"""
        item = self.config_schema.get(name, {})
        fallback = item.get("default", default)
        return ConfigManager.get(self.full_config_key(name), fallback)

    # ---- 注入渲染 ----

    def matches(self, device: DeviceState) -> bool:
        """设备是否属于本域。"""
        return device.domain in self.ha_domains

    def render(self, devices: List[DeviceState]) -> Optional[str]:
        """该域的注入行（零 I/O）；无可用设备返回 None。"""
        mine = [d for d in devices if self.matches(d) and d.available]
        if not mine:
            return None
        mine.sort(key=lambda d: d.name)
        limit = int(self.get_config("inject_limit", 8) or 0)
        omitted = 0
        if limit > 0 and len(mine) > limit:
            omitted = len(mine) - limit
            mine = mine[:limit]
        parts = [self.format_device(d) for d in mine]
        if omitted:
            parts.append(f"等 {omitted + limit} 台")
        return f"{self.display_name}: " + " | ".join(parts)

    def format_device(self, device: DeviceState) -> str:
        """单台设备的注入片段。"""
        return f"{device.name}: {self.format_state(device)}"

    def format_state(self, device: DeviceState) -> str:
        """状态值的人性化文本（子类按域语义覆盖）。"""
        if device.state == "on":
            return "开"
        if device.state == "off":
            return "关"
        return device.state

    # ---- 控制 ----

    def actions(self) -> Dict[str, ActionSpec]:
        """该域支持的控制动作表（action 名 → 服务调用映射）。"""
        return {
            "turn_on": ActionSpec("turn_on", "打开"),
            "turn_off": ActionSpec("turn_off", "关闭"),
            "toggle": ActionSpec("toggle", "切换"),
        }

    def build_service_call(self, action: str, raw_value: str = "") -> Tuple[str, Dict[str, Any]]:
        """把外部动作请求解析为平台服务调用（非法输入抛 SmartHomeCallError）。"""
        candidates = self.actions()
        spec = candidates.get(action.strip())
        if spec is None:
            known = ", ".join(candidates) or "（无）"
            raise SmartHomeCallError(
                f"域「{self.display_name}」不支持动作: {action or '(未提供)'}"
                f"（可用: {known}）",
                ErrorCause.PARAM,
            )
        data: Dict[str, Any] = {}
        if spec.value_param is not None:
            value = raw_value.strip()
            if not value:
                raise SmartHomeCallError(
                    f"动作 {action} 需要 value 参数（{spec.value_hint}）",
                    ErrorCause.PARAM,
                )
            try:
                data[spec.value_param] = spec.convert(value) if spec.convert else value
            except (ValueError, TypeError) as exc:
                raise SmartHomeCallError(
                    f"动作 {action} 的值非法: {raw_value}"
                    f"（{spec.value_hint}；{exc}）",
                    ErrorCause.PARAM,
                ) from exc
        return spec.service, data

    # ---- 序列化（Web API / AI 工具共用） ----

    def describe(self, devices: List[DeviceState]) -> Dict[str, Any]:
        """域状态与配置的可序列化描述（面板与 AI 工具数据驱动渲染）。

        配置项类型口径与统一配置架构一致（ConfigRegistry 的 type_name）。
        """
        from core.config import ConfigRegistry

        mine = [d for d in devices if self.matches(d)]
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
            "ha_domains": list(self.ha_domains),
            "enabled": self.is_enabled(),
            "default_enabled": self.default_enabled,
            "device_count": len(mine),
            "available_count": sum(1 for d in mine if d.available),
            "actions": {
                name: {
                    "description": spec.description,
                    "accepts_value": spec.value_param is not None,
                    "value_hint": spec.value_hint,
                }
                for name, spec in self.actions().items()
            },
            "configs": configs,
        }


def device_domain(cls: type[DeviceDomain]) -> type[DeviceDomain]:
    """注册设备域组件（实例化入注册表；同名覆盖）。"""
    if not cls.key:
        raise ValueError(f"{cls.__name__} 缺少 key 声明")
    if not cls.ha_domains:
        raise ValueError(f"{cls.__name__} 缺少 ha_domains 声明")
    instance = cls()
    _DOMAINS[cls.key] = instance
    for ha_domain in cls.ha_domains:
        _BY_HA_DOMAIN.setdefault(ha_domain, instance)
    return cls


def get_domain(key: str) -> Optional[DeviceDomain]:
    """按 key 取域组件实例。"""
    return _DOMAINS.get(key)


def all_domains() -> List[DeviceDomain]:
    """全部域组件（按注入排序权重排序）。"""
    return sorted(_DOMAINS.values(), key=lambda d: d.priority)


def domain_for_ha(ha_domain: str) -> Optional[DeviceDomain]:
    """按平台设备域（entity_id 前缀）取所属域组件。"""
    return _BY_HA_DOMAIN.get(ha_domain)


def config_entries() -> Dict[str, Any]:
    """汇总全部域的配置项（键已加前缀），供注册进 entity/smart_home 组。"""
    entries: Dict[str, Any] = {}
    for domain in all_domains():
        entries[domain.enabled_key] = {
            "description": f"启用「{domain.display_name}」设备域（上下文注入与控制）",
            "default": domain.default_enabled,
        }
        for name, item in domain.config_schema.items():
            entries[domain.full_config_key(name)] = dict(item)
    return entries


def render_context(devices: List[DeviceState]) -> str:
    """组装当前注入文本（遍历启用域，按优先级排序拼接）。"""
    parts: List[str] = []
    for domain in all_domains():
        if not domain.is_enabled():
            continue
        try:
            text = domain.render(devices)
        except Exception as exc:
            log(f"设备域 {domain.key} 渲染异常: {exc}", "WARNING", tag=_LOG_TAG)
            continue
        if text:
            parts.append(text.strip())
    if not parts:
        return ""
    return "[智能家居]\n" + "\n".join(parts)
