"""自主决策系统：AI 自行决定回复/反思/主动行动。"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Union

from core.log import log


# ======================================================================
# Mind 运行阶段
# ======================================================================

class MindPhase(str, Enum):
    """Mind 运行阶段（用于状态展示和调试）。"""

    IDLE = "idle"
    ACCEPTING = "accepting"
    DECIDING = "deciding"
    RECALLING = "recalling"
    LLM_CALLING = "llm_calling"
    TOOL_EXECUTING = "tool_executing"
    REPLYING = "replying"
    INTROSPECTING = "introspecting"


# ======================================================================
# 决策类型
# ======================================================================

class DecisionType(str, Enum):
    """AI 可执行的决策类型。"""

    REPLY = "reply"
    REFLECT = "reflect"
    REMEMBER = "remember"
    PROACTIVE = "proactive"
    TOOL_ACTION = "tool_action"
    PLAN = "plan"
    SELF_TASK = "self_task"
    IDLE = "idle"


@dataclass
class Decision:
    """一条决策。"""

    type: DecisionType
    target: Optional[str] = None
    reason: str = ""
    priority: int = 0
    content: str = ""
    params: Dict[str, Any] = field(default_factory=dict)


# ======================================================================
# 任务模型
# ======================================================================

class TaskType(str, Enum):
    """PFC 任务类型。"""

    MESSAGE = "message"
    SELF_TASK = "self_task"
    ERROR = "error"
    PROFILE = "profile"


@dataclass
class MindTask:
    """通用任务条目（非消息类任务）。"""

    task_type: TaskType
    scope: str = ""
    preview: str = ""
    adapter_key: str = ""
    uid: Union[int, str] = 0
    group_id: Union[int, str] = 0
    timestamp: float = field(default_factory=time.time)
    metadata: Dict[str, Any] = field(default_factory=dict)


# ======================================================================
# 消息 & 态势
# ======================================================================

@dataclass
class PendingMessage:
    """待处理消息的摘要信息。"""

    scope: str
    preview: str
    uid: Union[int, str] = 0
    group_id: Union[int, str] = 0
    timestamp: float = 0.0
    adapter_key: str = ""


@dataclass
class SituationContext:
    """当前态势上下文。"""

    pending_messages: List[PendingMessage] = field(default_factory=list)
    pending_tasks: List[MindTask] = field(default_factory=list)
    pending_profile_count: int = 0
    recent_memories: List[str] = field(default_factory=list)
    last_reflect_time: float = 0.0
    current_time: float = field(default_factory=time.time)
    is_heartbeat: bool = False
    connected_channels: List[str] = field(default_factory=list)
    active_goals: List[str] = field(default_factory=list)
    heartbeat_log: str = ""

    @property
    def has_pending(self) -> bool:
        return len(self.pending_messages) > 0 or len(self.pending_tasks) > 0

    @property
    def hours_since_reflect(self) -> float:
        if self.last_reflect_time <= 0:
            return 999.0
        return (self.current_time - self.last_reflect_time) / 3600.0

    def to_summary(self) -> str:
        """生成供元决策 prompt 使用的态势摘要。"""
        lines: list[str] = []
        lines.append(f"[当前时间] {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(self.current_time))}")
        hours = self.hours_since_reflect
        lines.append(f"[距上次反思] {hours:.1f} 小时")
        if hours < 0.5:
            lines.append("  [注意] 距上次反思时间很短，避免重复反思，优先做其他事情")
        elif self.is_heartbeat and hours > 1.0:
            lines.append(f"  [建议] 距上次反思已 {hours:.1f} 小时，建议执行 reflect 进行阶段性反思")

        if self.connected_channels:
            lines.append(f"[通信通道] {len(self.connected_channels)} 个：{', '.join(self.connected_channels)}")
            lines.append("  提示：你可以通过 send_to 工具向任意通道主动发送消息")
        else:
            lines.append("[通信通道] 无已连接通道")

        if self.pending_messages:
            lines.append(f"[待处理消息] {len(self.pending_messages)} 条：")
            for pm in self.pending_messages:
                source = f"[来自{pm.adapter_key}]" if pm.adapter_key else ""
                lines.append(f"  - {pm.scope}{source}: {pm.preview[:200]}")
        else:
            lines.append("[待处理消息] 无")

        if self.pending_tasks:
            lines.append(f"[待处理任务] {len(self.pending_tasks)} 个：")
            for t in self.pending_tasks:
                lines.append(f"  - [{t.task_type.value}] {t.preview[:80]}")

        if self.pending_profile_count > 0:
            if hours < 0.5:
                lines.append(f"[待整理画像] {self.pending_profile_count} 个实体等待画像分析（距上次反思太近，稍后再做）")
            else:
                lines.append(f"[待整理画像] {self.pending_profile_count} 个实体等待画像分析（心跳时执行 reflect）")

        if self.recent_memories:
            lines.append(f"[近期记忆] {len(self.recent_memories)} 条：")
            for mem in self.recent_memories:
                lines.append(f"  - {mem}")

        if self.active_goals:
            lines.append(f"[活跃目标] {len(self.active_goals)} 个：")
            for g in self.active_goals:
                lines.append(f"  - {g}")
            if self.is_heartbeat:
                lines.append(f"  [建议] 有 {len(self.active_goals)} 个活跃目标未完成，考虑执行 plan 推进")
        else:
            lines.append("[活跃目标] 无")
            if self.is_heartbeat and self.hours_since_reflect > 1:
                lines.append("  [建议] 无活跃目标，可通过 plan 创建新的目标规划")

        if self.is_heartbeat:
            lines.append("[触发源] 心跳（无外部刺激，可自主行动）")

        if self.heartbeat_log:
            lines.append("[历史心跳]")
            lines.append(self.heartbeat_log)

        return "\n".join(lines)


# ======================================================================
# 元决策：基于 Tool Calling 的决策系统
# ======================================================================

META_DECISION_SYSTEM = """你是决策核心。分析当前态势，调用 decide 工具表达决定。

决策优先级（从高到低）：
1. reply — 有待处理消息时必须回复，同一 scope 只需一个 reply 决策
2. tool_action — 有需要立即执行的工具操作
3. plan — 有活跃目标需要推进，或需要创建新计划
4. reflect — 心跳时距上次反思较久，或有待整理画像
5. remember — 只记全新的重要信息，要克制
6. proactive — 有充分理由主动联系某人时才使用
7. idle — 心跳无事可做时

决策规则：
- 消息预览可能被截断，不影响决策——reply 阶段可看到完整内容
- 同一 scope 的多条消息只需一个 reply，不要重复
- 待处理任务（self_task）需要你选择 tool_action 来执行
- 可以连续调用多个 decide 来表达多个决策
- tool_action 时在 content 中描述要执行的操作"""

DECISION_TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "decide",
            "description": "输出一个决策。有消息时 type 填 reply，心跳无消息时按需选择其他类型。",
            "parameters": {
                "type": "object",
                "properties": {
                    "type": {
                        "type": "string",
                        "enum": ["reply", "reflect", "remember", "proactive",
                                 "tool_action", "plan", "self_task", "idle"],
                        "description": "决策类型",
                    },
                    "target": {
                        "type": "string",
                        "description": "目标标识（reply 填消息来源 scope，如 user_123）",
                    },
                    "reason": {
                        "type": "string",
                        "description": "决策原因（简短说明）",
                    },
                    "priority": {
                        "type": "integer",
                        "description": "优先级（10=高，1=低，默认 5）",
                    },
                    "content": {
                        "type": "string",
                        "description": "附加内容（remember=要记的信息，proactive=消息内容，tool_action=要执行的操作描述，plan=规划说明）",
                    },
                },
                "required": ["type"],
            },
        },
    },
]


def build_meta_decision_messages(
    personality_msgs: List[Dict],
    situation: SituationContext,
    memory_context: List[Dict],
) -> List[Dict]:
    """构建元决策 prompt 完整消息列表。"""
    system_parts = [m["content"] for m in personality_msgs if m.get("content")]
    system_parts.append(META_DECISION_SYSTEM)
    messages: List[Dict] = [{"role": "system", "content": "\n\n".join(system_parts)}]
    for m in memory_context:
        messages.append({**m, "role": "user"} if m.get("role") == "system" else m)
    messages.append({"role": "user", "content": situation.to_summary()})
    return messages


def parse_decisions_from_tool_calls(
    result_tool_calls: list,
    situation: Optional[SituationContext] = None,
) -> List[Decision]:
    """从 LLM 的原生 tool_calls 解析决策列表。

    兜底：如果模型未调用 decide 工具但有待处理消息，回退为 reply。
    """
    decisions: List[Decision] = []

    for tc in result_tool_calls:
        if tc.name != "decide":
            continue
        try:
            args = json.loads(tc.arguments) if isinstance(tc.arguments, str) else tc.arguments
        except (json.JSONDecodeError, TypeError):
            continue

        dtype = args.get("type", "idle")
        try:
            dt = DecisionType(dtype)
        except ValueError:
            continue

        decisions.append(Decision(
            type=dt,
            target=args.get("target"),
            reason=args.get("reason", ""),
            priority=int(args.get("priority", 5)),
            content=args.get("content", ""),
            params=args.get("params", {}),
        ))

    if decisions:
        return _deduplicate_reply_decisions(decisions)

    # 兜底：模型未调用 decide 工具，但有待处理消息时不能丢
    if situation and situation.pending_messages:
        log(f"元决策未产生 decide 调用，回退为 reply {len(situation.pending_messages)} 条消息", "WARNING", tag="思维")
        return [
            Decision(type=DecisionType.REPLY, target=pm.scope, reason="兜底回复", priority=10)
            for pm in situation.pending_messages
        ]

    return [Decision(type=DecisionType.IDLE, reason="无决策")]


def _deduplicate_reply_decisions(decisions: List[Decision]) -> List[Decision]:
    """同一 scope 的 REPLY 决策只保留优先级最高的一个。"""
    result: List[Decision] = []
    seen_reply_targets: dict[Optional[str], int] = {}
    for d in decisions:
        if d.type == DecisionType.REPLY and d.target:
            if d.target in seen_reply_targets:
                idx = seen_reply_targets[d.target]
                if d.priority > result[idx].priority:
                    result[idx] = d
                continue
            seen_reply_targets[d.target] = len(result)
        result.append(d)
    return result
