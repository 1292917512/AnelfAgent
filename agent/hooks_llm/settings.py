"""代码钩子的可调参数接入统一配置面，AI 与 Web 使用同一读写入口。"""

from __future__ import annotations

import math
from dataclasses import replace
from typing import TYPE_CHECKING

from core.config import ConfigItem, ConfigRegistry, ConfigValueType, get_config
from core.hooks import HookMode

if TYPE_CHECKING:
    from .spec import LLMHookSpec


def config_keys(spec: LLMHookSpec) -> dict[str, str]:
    fields = ("enabled", "mode", "cooldown_seconds") if spec.source == "task" else (
        "enabled", "mode", "context", "model", "max_iterations", "cooldown_seconds")
    return {field: spec.config_key if field == "enabled" and spec.config_key else f"llm_hook_{spec.name}_{field}"
            for field in fields}


def register_settings(spec: LLMHookSpec) -> None:
    """声明可热更的配置；代码仍持有事件、处理函数和工具权限。"""
    fields = {
        "enabled": ("是否启用钩子；不取消已经受理的执行", ConfigValueType.BOOLEAN, None, None, None),
        "mode": ("执行模式：sync 等待结束；async 后台协作。同步模式不使用防抖延迟", ConfigValueType.ENUM, ["sync", "async"], None, None),
        "context": ("上下文继承：transcript 原对话完整过程；lean 人设与固定记忆；none 不继承。跨模型或工具目录变化仍可能影响缓存", ConfigValueType.ENUM, ["transcript", "lean", "none"], None, None),
        "model": ("执行模型，留空跟随默认模型", ConfigValueType.MODEL, None, None, None),
        "max_iterations": ("单次执行的最大思考轮数；不设置整体时间截止", ConfigValueType.INTEGER, None, 1, 100),
        "cooldown_seconds": ("同一会话两次触发的最小间隔，非执行超时", ConfigValueType.FLOAT, None, 0, 86400),
    }
    for field, key in config_keys(spec).items():
        if field == "enabled" and spec.config_key and ConfigRegistry.get_item(key):
            continue
        description, value_type, options, minimum, maximum = fields[field]
        default = getattr(spec, field)
        ConfigRegistry.register(ConfigItem(
            key=key, group=f"hooks_llm/{spec.name}", description=description,
            default_value=default.value if hasattr(default, "value") else default,
            value_type=value_type, enum_options=options, min_value=minimum, max_value=maximum,
        ))


def effective_spec(spec: LLMHookSpec) -> LLMHookSpec:
    """调度前读取配置并用注册元数据校验，非法外部值保留声明默认。"""
    values = {}
    from .spec import HOOK_EVENT_LLM_END, LLM_END_MIN_COOLDOWN_SECONDS, HookContextMode
    for field, key in config_keys(spec).items():
        item = ConfigRegistry.get_item(key)
        if item is None:
            continue
        try:
            value = item.clamp(item.coerce_value(get_config(key, getattr(spec, field))))
            if isinstance(value, float) and not math.isfinite(value):
                continue
            if item.enum_options and value not in item.enum_options:
                continue
            values[field] = value
        except (ValueError, TypeError):
            continue
    values["mode"] = HookMode(values.get("mode", spec.mode))
    values["context"] = HookContextMode(values.get("context", spec.context))
    if spec.event == HOOK_EVENT_LLM_END:
        values["cooldown_seconds"] = max(LLM_END_MIN_COOLDOWN_SECONDS, values.get("cooldown_seconds", spec.cooldown_seconds))
    if values["mode"] == HookMode.SYNC:
        values["debounce_seconds"] = 0.0
    return replace(spec, **values)
