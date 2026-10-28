"""单次思维执行的模式、共享容器与轮次状态。"""


from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Dict, List, Optional

from agent.channel.delivery import DeliveryStatus
from agent.mind.execution.result_pipeline import ToolResultPipeline

if TYPE_CHECKING:
    from agent.messages import Everything
    from agent.mind.background.tasks import (
        BackgroundTaskRegistry,
    )
    from agent.mind.guardrails import GuardrailController
    from agent.mind.mind import Mind
    from agent.skills.tag_context import SkillTagContext


class ThinkMode(str, Enum):
    """思维循环模式。"""

    REPLY = "reply"
    """对话模式：处理用户消息，通过工具发送回复。"""

    REFLECT = "reflect"
    """反思模式：正文作为内部产出，显式发送遵守任务权限。"""


class RoundOutcome(str, Enum):
    """阶段函数的处理结果，驱动主循环的后续动作。"""

    PROCEED = "proceed"
    """继续本轮的后续阶段。"""

    CONTINUE = "continue"
    """本轮已处理完，进入下一轮迭代。"""

    BREAK = "break"
    """结束整个思维循环。"""


@dataclass
class RoundState:
    """think_loop 的回合可变状态（计数器/水位/预算，每轮更新）。"""

    iteration: int = 0
    consecutive_empty_calls: int = 0
    consecutive_tool_errors: int = 0
    consecutive_security_leaks: int = 0
    consecutive_overflow_compressions: int = 0
    reflect_text_rounds: int = 0
    # 当前回复流程是否已有输出工具成功送达。
    output_sent: bool = False
    auto_reply_eligible: bool = False
    auto_reply_blocked: bool = False
    reply_delivery_status: DeliveryStatus = "pending"
    max_output_recoveries: int = 0
    # 上轮真实输入占用（口径归一：prompt 含/不含缓存两种记账下均为真实占用；
    # 独占口径——原生 Anthropic——下原始 prompt_tokens 不含缓存读/写，
    # 以其为锚会低估占用、压缩触发偏晚）
    last_input_tokens: int = 0
    # 最近一次 LLM 调用的供应商侧缓存用量（context_usage 事件与 exec_context 状态行展示用）
    last_cache_read_tokens: int = 0
    last_cache_creation_tokens: int = 0
    last_cache_hit_rate: float = 0.0
    # 端点是否回报缓存统计字段（False = 不可观测，状态行抑制注入而非谎报 0%）
    last_cache_observable: bool = True
    # 后台任务等待：本轮回复累计预算（秒）
    wait_budget: float = 0.0
    # 新消息并入基线水位（快照内最大 ts_ns）
    last_merged_ts: int = 0
    # 新消息并入 id 水位（同 ts_ns 并列的消息靠 id 精确决胜，0 表示无 id 水位回退 ts）
    last_merged_id: int = 0
    # 实体推送并入水位（快照后到达的推送才轮内注入，之前的已随短期记忆进 base）
    push_watermark: int = 0
    observed_cross_messages: dict[str, int] = field(default_factory=dict)
    observed_recollection: str = ""
    # plan 收敛标记：正常结束路径（_finish_round）已收敛时置位，finally 不重复收敛
    plan_finalized: bool = False
    # 本次会话是否被协作式中断终止（决定 finally 收敛 outcome）
    interrupted: bool = False
    # 中止时存在"已启动未完成"的工具调用（收束元消息提示可能已部分执行）
    partial_tool_abort: bool = False
    # 结束原因（completed/budget_exhausted；interrupted 经 state.interrupted 推断）
    completion_reason: str = "completed"


@dataclass
class ExecutionContext:
    """think_loop 的会话级上下文：循环期间共享的服务与消息容器。

    base_messages / tool_chain / active_tools 会在压缩、工具集重建、
    新消息并入时被整体替换，故以可变字段持有。
    """

    mind: "Mind"
    mode: ThinkMode
    anything: Optional["Everything"]
    adapter_key: str
    options: Optional[Dict]
    base_messages: List[Dict]
    tool_chain: List[Dict]
    execution_steps: List[str]
    collected_text: List[str]
    active_tools: List[Dict]
    current_scope: str
    interrupts: Any
    background: Optional["BackgroundTaskRegistry"]
    wait_per_round: float
    pure_tool_mode: bool
    mode_label: str
    guardrail: "GuardrailController"
    pipeline: ToolResultPipeline
    turn_id: str
    delta_emitter: Callable[[str, bool], Awaitable[None]]
    # scope 中断唤醒事件（在途 LLM 流/工具批与之中断竞争；无中断注册表时 None）
    abort_event: Any = None
    # 主回复工具日志（REPLY 模式逐调用落账，崩溃尾部账本；其余模式 None）
    journal: Any = None
    # 结束原因输出容器（调用方传入；None = 不收集）——置于全默认字段区
    completion: Optional[Dict] = None
    # 模式级禁用工具（内部任务禁外发 / leaf 禁委托）：执行侧拦截返回合成错误，
    # 不从 tools 数组移除——可见性（schema 数组全量冻结、跨调用字节一致）
    # 与权限（执行时拦截）分离，保证工具数组前缀缓存跨模式共享
    blocked_tools: frozenset = frozenset()
    # REFLECT 模式的工具选择器（mind.reflect 传入）：工具集版本变化重建
    # active_tools 时按同批选择器还原精简目录；REPLY 模式不使用
    reflect_tool_selectors: tuple[str, ...] = ()
    require_output: bool = False
    summary_only: bool = False
    skill_context: SkillTagContext | None = None
