"""智能家居上下文提供者 — 把启用设备域的实时状态注入 PFC volatile 层。

设备状态经 WebSocket 实时推入缓存，provide() 只读内存快照（零 I/O，
满足 provider 1s 超时约束）：未配置平台 / 全部设备域无可用设备时返回
None 不注入；已配置但连接中断时注入一行不可用提示（AI 可感知而非静默）。

Model Experience:
    模型看到什么：默认仅当前会话唤醒 smart_home 后，在 volatile 区注入按房间分组的快照。
        普通任务无设备注入；阶段结束沿用 deactivate_tool_group，不另设关键词猜测机制。
    token 影响：沉睡时零状态 token；唤醒后按完整条目限制到 2400 字符。
        框架按字符/4 粗估约 600 token，中文实际 token 用量取决于模型。
    缓存影响：仅动态区变化，不触碰稳定前缀；面板预览与实际注入共用判断。
"""

from __future__ import annotations

from typing import Optional

from core.config import ConfigManager, get_config_bool
from core.context_provider import ProviderSnapshot
from core.entity import EntityRegistry
from entities._sdk import context_provider, tool_group_rounds_left

from . import framework
from .manager import get_smart_home_manager


def context_content(scope: str = "", *, preview: bool = False) -> str:
    """零 I/O 的快照；预览仅绕过会话唤醒门控，其余条件与实际注入一致。"""
    if not get_config_bool("smart_home_context_inject", True):
        return ""
    if not EntityRegistry.group_has_enabled_tools("smart_home"):
        return ""
    if context_mode() == "on_demand" and not preview and tool_group_rounds_left("smart_home", scope) <= 0:
        return ""
    manager = get_smart_home_manager()
    status = manager.status()
    if not status.get("configured"):
        return ""
    if not status.get("connected"):
        return "[智能家居] 全部供应商连接中断，设备状态不可用"
    devices = manager.devices()
    stale = sum(1 for device in devices if device.stale)
    content = framework.render_context(devices)
    if stale:
        content = (content or "[智能家居]") + f"\n另有 {stale} 台设备状态已过期，不作为实时依据。"
    return content


def context_mode() -> str:
    """读取感知策略；未识别的配置保守地回退按需模式。"""
    return "always" if ConfigManager.get("smart_home_context_mode", "on_demand") == "always" else "on_demand"


@context_provider(
    name="smart_home", priority=50, max_tokens=600,
    group="smart_home", inject_key="smart_home_context_inject",
)
class SmartHomeContextProvider:
    """注入智能家居设备实时状态（启用设备域的聚合快照）。"""

    async def provide(self, scope: str) -> Optional[ProviderSnapshot]:
        content = context_content(scope)
        if not content:
            return None
        return ProviderSnapshot(content=content, ready=True)
