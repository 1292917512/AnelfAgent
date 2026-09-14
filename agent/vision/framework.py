"""视觉源组件框架 — 核心视觉能力的可插拔来源注册表。

新增视觉源只需：定义 :class:`VisualSource` 子类并经
``entities._sdk.register_vision_source`` 注册（实体/插件组件化接入）。

组件契约：
- 即时型（``poll_interval = 0``）：只支持即时取帧（``capture``），
  如外部推送源（帧由外部推入缓冲，自身无捕获）；
- 轮询型（``poll_interval > 0``）：watcher 按全局 vision_watch_interval_s
  配置定频调 ``capture`` 持续采帧（源不定频，该值仅作类型标记），
  如屏幕源。

Model Experience:
    模型看到什么 —— 各源状态经 vision context_provider 注入 volatile 层，
        变化源的最新一帧以图片附件直注（视觉模型）或标签引用（降级）；
    token 影响 —— 变化驱动，静态画面零图片 token；
    缓存影响 —— volatile 尾部动态区，不触碰 stable/conversation 前缀。
"""

from __future__ import annotations

from typing import ClassVar, Dict, List, Optional

from core.config import get_config

from .capture import CapturedFrame

_LOG_TAG = "视觉"
_SOURCES: Dict[str, "VisualSource"] = {}


class VisualSource:
    """视觉源组件基类（声明类属性即接入框架）。"""

    key: ClassVar[str] = ""
    """源全局唯一标识（小写英文；外部推送源的 key 为 external:<name>）。"""
    display_name: ClassVar[str] = ""
    description: ClassVar[str] = ""
    poll_interval: ClassVar[float] = 0.0
    """轮询型标记（>0 即支持盯屏轮询；实际捕获间隔读全局
    vision_watch_interval_s 配置，源不单独定频）。"""
    can_capture: ClassVar[bool] = True
    """是否支持即时取帧（外部推送源为 False——帧只能由外部推入）。"""

    async def capture(self) -> Optional[CapturedFrame]:
        """即时取帧（零/轻 I/O 到允许 I/O 均可，由调用方在线程/任务中调度）。"""
        return None


def register_source(source: VisualSource) -> None:
    """注册视觉源组件实例（同 key 覆盖）。"""
    if not source.key:
        raise ValueError(f"{type(source).__name__} 缺少 key 声明")
    _SOURCES[source.key] = source


def unregister_source(key: str) -> None:
    _SOURCES.pop(key, None)


def get_source(key: str) -> Optional[VisualSource]:
    return _SOURCES.get(key)


def disabled_sources() -> set[str]:
    """已停用的视觉源集合（vision_disabled_sources 配置，热读取）。"""
    raw = str(get_config("vision_disabled_sources", "") or "")
    return {s.strip() for s in raw.split(",") if s.strip()}


def is_enabled(key: str) -> bool:
    """视觉源是否处于激活状态（未注册的外部推送源 key 也可判定）。"""
    return key not in disabled_sources()


def set_enabled(key: str, enabled: bool) -> None:
    """激活/停用视觉源（持久化到 vision_disabled_sources 配置）。"""
    from core.config import ConfigManager
    disabled = disabled_sources()
    if enabled:
        disabled.discard(key)
    else:
        disabled.add(key)
    ConfigManager.set("vision_disabled_sources", ",".join(sorted(disabled)))
    ConfigManager.save()


def all_sources() -> List[VisualSource]:
    return sorted(_SOURCES.values(), key=lambda s: s.key)


def enabled_sources() -> List[VisualSource]:
    """处于激活状态的视觉源（上下文注入/AI 工具的作用范围）。"""
    return [s for s in all_sources() if is_enabled(s.key)]


def pollable_sources() -> List[VisualSource]:
    """支持盯屏轮询的源（含已停用；门控由调用方判定）。"""
    return [s for s in all_sources() if s.poll_interval > 0 and s.can_capture]
