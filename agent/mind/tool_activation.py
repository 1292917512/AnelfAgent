"""工具沉睡/激活状态机（参考 nekro-agent prompt_activation）。

声明 ``allow_sleep=True`` + ``sleep_brief`` 的工具默认处于沉睡状态：
不出现在 LLM 的完整 schema 中，仅在工具目录里展示简短描述（低 token）。
AI 需要时调用 ``activate_tool_group`` 唤醒整个分组，唤醒后持续 N 轮对话，
轮数耗尽自动回到沉睡状态。

激活状态按对话 scope 隔离（不同用户/群聊互不影响），
当前 scope 通过 contextvars 绑定，异步任务间互不串扰。
"""
from __future__ import annotations

import json
from contextvars import ContextVar
from typing import Dict, List, Optional

from core.config import get_config_int
from core.entity import EntityRegistry, EntityType
from core.log import log
from entities._sdk import deferred_tool

_DEFAULT_ACTIVE_ROUNDS = 3
_MAX_ACTIVE_ROUNDS = 20

# 当前思维会话的对话 scope（由 think_loop 在会话期间绑定）
_current_scope: ContextVar[str] = ContextVar("tool_activation_scope", default="")


def bind_scope(scope: str):
    """绑定当前对话 scope，返回可复位的 token（供 think_loop 使用）。"""
    return _current_scope.set(scope)


def reset_scope(token) -> None:
    """复位 bind_scope 绑定的 scope。"""
    _current_scope.reset(token)


def current_owner_scope() -> str:
    """解析后台任务的归属会话 scope（完成通知的路由目标）。

    委托链经 usage_scope 绑定的父会话 scope 优先——子代理（reflect 一次性
    scope）内启动的后台任务必须归属发起会话，完成通知才能路由回对话；
    未绑定或绑定值非会话 scope（user_/group_）时回退当前激活 scope。
    与 llm_invoker 的 scope 解析链同构（entity_scope > usage_scope > 激活上下文）。
    """
    from agent.mind.scope_usage import current_usage_scope
    bound = current_usage_scope()
    if bound.startswith(("user_", "group_")):
        return bound
    return ToolActivationManager.current_scope()


class ToolActivationManager:
    """工具分组激活状态管理：scope -> group -> 剩余轮数。"""

    def __init__(self) -> None:
        self._scope_rounds: Dict[str, Dict[str, int]] = {}
        # 版本号：每次激活/续期/消耗/清除时递增，供 think_loop 检测工具集变化
        self._version: int = 0

    @property
    def version(self) -> int:
        """当前工具集版本号（激活/续期/消耗/清除时递增）。"""
        return self._version

    @staticmethod
    def current_scope() -> str:
        """解析当前对话 scope（未绑定时使用全局作用域）。"""
        return _current_scope.get() or "_global"

    @staticmethod
    def _clamp_rounds(rounds: Optional[int]) -> int:
        default = get_config_int("tool_gate_default_active_rounds", _DEFAULT_ACTIVE_ROUNDS)
        maximum = get_config_int("tool_gate_max_active_rounds", _MAX_ACTIVE_ROUNDS)
        value = rounds if rounds and rounds > 0 else default
        return max(1, min(value, maximum))

    def activate(self, group: str, rounds: Optional[int] = None, scope: str = "") -> int:
        """激活分组，返回实际生效轮数。"""
        scope = scope or self.current_scope()
        final_rounds = self._clamp_rounds(rounds)
        self._scope_rounds.setdefault(scope, {})[group] = final_rounds
        self._version += 1
        log(f"工具分组已激活: [{group}] scope={scope} 持续 {final_rounds} 轮", tag="门控")
        return final_rounds

    def extend(self, group: str, rounds: Optional[int] = None, scope: str = "") -> int:
        """续期已激活的分组，返回新的剩余轮数。"""
        scope = scope or self.current_scope()
        current = self._scope_rounds.get(scope, {}).get(group, 0)
        added = self._clamp_rounds(rounds)
        maximum = get_config_int("tool_gate_max_active_rounds", _MAX_ACTIVE_ROUNDS)
        final_rounds = min(current + added, maximum)
        self._scope_rounds.setdefault(scope, {})[group] = final_rounds
        self._version += 1
        log(f"工具分组已续期: [{group}] scope={scope} 剩余 {final_rounds} 轮", "DEBUG", tag="门控")
        return final_rounds

    def is_active(self, group: str, scope: str = "") -> bool:
        """分组在当前 scope 下是否处于激活状态。"""
        scope = scope or self.current_scope()
        return self._scope_rounds.get(scope, {}).get(group, 0) > 0

    def rounds_left(self, group: str, scope: str = "") -> int:
        """分组在当前 scope 下的剩余激活轮数（0 = 沉睡）。"""
        scope = scope or self.current_scope()
        return self._scope_rounds.get(scope, {}).get(group, 0)

    def active_groups(self, scope: str = "") -> Dict[str, int]:
        """返回当前 scope 下所有激活分组及其剩余轮数。"""
        scope = scope or self.current_scope()
        return dict(self._scope_rounds.get(scope, {}))

    def consume_round(self, scope: str = "") -> List[str]:
        """对话会话结束时消耗一轮激活周期，返回本轮到期沉睡的分组列表。

        粘性模式（tool_activation_sticky，默认开）：激活的分组不再过期——
        激活/过期会改变 tools 数组（位于请求最前），每次变化重写其后全部
        缓存前缀；粘性行为让工具集只增不减，跨会话字节稳定。
        """
        from core.config import get_config_bool
        if get_config_bool("tool_activation_sticky", True):
            return []
        scope = scope or self.current_scope()
        groups = self._scope_rounds.get(scope)
        if not groups:
            return []
        expired: List[str] = []
        for group in list(groups.keys()):
            groups[group] -= 1
            if groups[group] <= 0:
                del groups[group]
                expired.append(group)
        if not groups:
            self._scope_rounds.pop(scope, None)
        if expired:
            self._version += 1
            log(f"工具分组回到沉睡: {', '.join(expired)} (scope={scope})", "DEBUG", tag="门控")
        return expired

    def clear_scope(self, scope: str) -> None:
        """清空指定 scope 的全部激活状态。"""
        if scope in self._scope_rounds:
            self._scope_rounds.pop(scope, None)
            self._version += 1

    def notify_tools_changed(self) -> None:
        """工具集成员变化通知（插件装卸载等不改激活状态的成员增删）。

        递增版本号，驱动下一轮对话重组装工具 schema 并重算 stable 层指纹——
        新增工具进入目录、注销工具从冻结数组滤除都依赖这次重建。
        """
        self._version += 1


# 全局单例
tool_activation = ToolActivationManager()


# ------------------------------------------------------------------
# AI 可调用工具：激活工具分组
# ------------------------------------------------------------------

@deferred_tool(
    name="activate_tool_group",
    group="thinking", tags=["always"], source="mind.core",
    description="激活一个沉睡的工具分组，唤醒后该分组的完整工具将在后续轮次可用。"
    "当工具目录中某个分组标记为[沉睡]且你需要使用它时调用。",
)
def _activate_tool_group_tool(group: str, rounds: int = 0) -> str:
    """激活沉睡的工具分组。

    Args:
        group: 要激活的工具分组名（工具目录中标记[沉睡]的分组）
        rounds: 持续轮数，不传使用默认值（激活期间每轮对话消耗一轮）
    """
    sleepable = EntityRegistry.get_sleepable_groups()
    if group not in sleepable:
        # 分组存在但非沉睡管理：引导改用动态发现，避免 AI 反复重试激活
        has_tools = any(
            e.entity_type == EntityType.TOOL and e.enabled
            for e in EntityRegistry.get_by_group(group)
        )
        if has_tools:
            return json.dumps({
                "error": f"分组 '{group}' 的工具不受沉睡管理，无法通过 activate_tool_group 唤醒。",
                "hint": f"请改用 list_entity_methods(group=\"{group}\") 动态发现该分组的工具，"
                        "发现后即可直接调用。",
            }, ensure_ascii=False)
        available = ", ".join(sorted(sleepable.keys())) or "（无）"
        return json.dumps({
            "error": f"分组 '{group}' 不是可沉睡分组或不存在。",
            "available_sleeping_groups": available,
        }, ensure_ascii=False)

    final_rounds = tool_activation.activate(group, rounds or None)

    # 返回该分组的工具摘要，让 AI 立即了解可用方法（无需再查 list_entity_methods）
    tools_summary = []
    for e in EntityRegistry.get_by_group(group):
        if e.entity_type.value != "tool" or not e.enabled:
            continue
        params = [p.name for p in e.meta.get("params", []) if p.required]
        tools_summary.append({
            "name": e.name,
            "description": e.description,
            "required_params": params,
        })

    return json.dumps({
        "ok": True,
        "group": group,
        "active_rounds": final_rounds,
        "hint": f"分组已激活，持续 {final_rounds} 轮对话。以下是该分组的工具摘要，"
                f"完整 schema 将从下一轮开始自动出现在工具列表中。",
        "tools": tools_summary,
    }, ensure_ascii=False)
