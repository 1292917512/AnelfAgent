"""钩子规格与注册表。

声明式注册面的数据层：LLMHookSpec 描述一个钩子（事件 + 上下文档位 +
条件门控 + 治理参数 + 执行体），HookRegistry 按 event 多值索引全部注册
的钩子（同一钩子位置支持多钩子并行拉起，按 priority 排序）。

本模块为纯数据 + 注册表，不依赖 mind / 事件总线，可独立测试；
运行时装配与事件订阅在 runtime.py，执行在 executor.py。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Dict, List, Optional

from core.hooks import HookMode

# 钩子可订阅的事件白名单（只开放有明确语义的生命周期事件，
# 不把 event_bus 全量事件暴露成触发点——防任务面变成另一个事件总线）
HOOK_EVENT_AFTER_REPLY = "after_reply"
HOOK_EVENT_CONTEXT_PRESSURE = "context_pressure"
HOOK_EVENT_DELEGATION_RESOLVED = "delegation_resolved"
HOOK_EVENT_LLM_END = "llm_end"
HOOK_EVENTS = frozenset({
    HOOK_EVENT_AFTER_REPLY,
    HOOK_EVENT_CONTEXT_PRESSURE,
    HOOK_EVENT_DELEGATION_RESOLVED,
    HOOK_EVENT_LLM_END,
})

# llm_end 是高频事件（一次多轮回复触发十余次）：钩子的最小触发间隔下限，
# 防"每轮 LLM 后都拉起一段 LLM 工作"的成本失控。声明更小值也会被钳到此下限
LLM_END_MIN_COOLDOWN_SECONDS = 20.0


class HookContextMode(str, Enum):
    """钩子继承的上下文档位。"""

    NONE = "none"                # 仅指令，不带上下文
    LEAN = "lean"                # 人设 + 工具 + 永久记忆（精简前缀）
    TRANSCRIPT = "transcript"    # 触发时刻消息链冻结快照


# 钩子执行体签名：接收 HookContext，返回可选的产出文本（写入 registry/journal）
HookHandler = Callable[["HookContext"], Awaitable[Optional[str]]]
# 条件门控签名：接收事件 payload，返回是否触发（不满足零开销跳过）
# typing.Callable 在部分 Python 3.12.x 环境对双括号语法有兼容性问题，
# 用 lambda 形式的 Protocol 或延迟评估规避（运行时仅作类型标注用途）
if TYPE_CHECKING:
    HookWhen = Callable[[Dict[str, Any]], bool]
else:
    HookWhen = Any  # 运行时仅作类型别名，不参与求值


@dataclass(frozen=True, slots=True)
class LLMHookSpec:
    """一个 LLM 钩子的完整声明（注册即生效）。

    声明保持不可变；每次调度读取统一配置面的覆盖值。
    """

    name: str
    """全局唯一钩子名（日志/统计/启停/治理归属键）。"""
    event: str
    """订阅的生命周期事件（须在 HOOK_EVENTS 白名单内）。"""
    handler: HookHandler
    """执行体：以 HookContext 为输入的协程。"""
    mode: HookMode = HookMode.ASYNC
    context: HookContextMode = HookContextMode.TRANSCRIPT
    """继承的上下文档位。"""
    when: Optional[HookWhen] = None
    """条件门控：None 恒触发；返回 False 零开销跳过。"""
    tool_tags: tuple[str, ...] = ()
    """reflect 工具选择器（空 = 复用回复级装配）。"""
    allow_output_tools: bool = False
    """是否放开外发工具（默认禁止，内部派生语义）。"""
    route_output: bool = True
    """产出路由：True=登记后台任务完成并经 unclaimed→wake_budget 通道唤醒
    主思维跟进处理；False=仅留完成记录（claimed 语义）供观测、不唤醒——
    内部治理类钩子（产出是自省报告、无需主思维跟进）用 False。"""
    max_iterations: int = 6
    """reflect 轮次预算。"""
    model: str = ""
    """执行模型 ID（空 = 默认主模型；可指定轻量模型省成本）。"""
    max_concurrent: int = 1
    """该钩子的并发上限（同一钩子自身的并行实例数）。"""
    cooldown_seconds: float = 0.0
    """per-hook per-scope 最小触发间隔（防抖/限频）。"""
    debounce_seconds: float = 0.0
    """同 scope 高频触发合并窗口（取窗口内最后一次快照）。"""
    priority: int = 50
    """同事件多钩子的调度顺序（值大先调度，同步钩子等待结束）。"""
    owner: str = ""
    """注册归属（模块/实体标识，便于审计与批量清理）。"""
    description: str = ""
    """人类可读描述（Web 面板展示）。"""
    source: str = "code"
    """注册来源：code=代码装饰器 / entity=实体桥接 / task=任务事件触发。"""
    enabled: bool = True
    """声明的默认启用状态。"""
    config_key: str = ""
    """复用模块已有的启用配置键；为空时由注册表生成。"""


@dataclass(slots=True)
class HookContext:
    """钩子执行体收到的上下文（快照 + 触发信息 + 声明的执行参数）。

    执行参数（tool_tags/max_iterations/allow_output_tools/model）由执行器
    从 LLMHookSpec 带入，是钩子声明的单一事实源——执行体调用 mind.reflect
    等思考入口时应原样透传，不得在执行体内另立常量。
    """

    name: str
    event: str
    scope: str
    """触发来源会话 scope（非会话事件为空串）。"""
    payload: Dict[str, Any]
    """事件原始 payload（只读）。"""
    context_mode: HookContextMode = HookContextMode.TRANSCRIPT
    """本次采用的上下文档位。"""
    messages: List[Dict[str, Any]] = field(default_factory=list)
    """按档位冻结的上下文快照，可直接作为 reflect 的 base_messages。"""
    inherited_tools: List[Dict[str, Any]] | None = None
    """触发轮的工具目录，保留顺序与内容以复用模型缓存。"""
    trigger: str = ""
    """触发来源标记（event 名 / 手动 / 任务事件）。"""
    tool_tags: tuple[str, ...] = ()
    """reflect 工具选择器（空 = 复用回复级装配）。"""
    max_iterations: int = 6
    """reflect 轮次预算。"""
    allow_output_tools: bool = False
    """是否放开外发工具（默认禁止，内部派生语义）。"""
    model: str = ""
    """执行模型 ID（空 = 默认主模型）。"""

    def transcript_available(self) -> bool:
        """transcript 档位快照是否真实带出了消息链。"""
        return self.context_mode is HookContextMode.TRANSCRIPT and bool(self.messages)


class HookRegistry:
    """钩子注册表：按 event 多值索引，全 classmethod 风格（仿 ContextProviderRegistry）。

    同一钩子位置（event）支持多个钩子；同 event 内按 priority
    降序调度，是否等待由 mode 决定。注册同名钩子按「后者覆盖前者」
    （幂等，便于热重载）。配置覆盖值持久化，注销只移除运行声明。
    """

    _by_event: Dict[str, List[LLMHookSpec]] = {}
    _by_name: Dict[str, LLMHookSpec] = {}

    @classmethod
    def register(cls, spec: LLMHookSpec) -> None:
        """注册钩子（同名覆盖，幂等）。enabled 初始值按声明或覆盖层。"""
        if spec.event not in HOOK_EVENTS:
            raise ValueError(
                f"非法钩子事件: {spec.event!r}（须为 {sorted(HOOK_EVENTS)} 之一）"
            )
        old = cls._by_name.get(spec.name)
        if not isinstance(spec.mode, HookMode):
            raise ValueError("mode 必须是 HookMode")
        if spec.mode == HookMode.SYNC and spec.debounce_seconds > 0:
            raise ValueError("同步钩子不能声明防抖延迟")
        if old is not None:
            cls.unregister(spec.name)
        cls._by_name[spec.name] = spec
        from .settings import register_settings
        register_settings(spec)
        bucket = cls._by_event.setdefault(spec.event, [])
        bucket.append(spec)
        bucket.sort(key=lambda s: s.priority, reverse=True)

    @classmethod
    def unregister(cls, name: str) -> bool:
        """按名注销，返回是否移除。"""
        spec = cls._by_name.pop(name, None)
        if spec is None:
            return False
        from core.config import ConfigRegistry
        ConfigRegistry.unregister_group(f"hooks_llm/{name}")
        bucket = cls._by_event.get(spec.event)
        if bucket is not None:
            cls._by_event[spec.event] = [s for s in bucket if s.name != name]
            if not cls._by_event[spec.event]:
                cls._by_event.pop(spec.event, None)
        return True

    @classmethod
    def unregister_by_owner(cls, owner: str) -> int:
        """按归属批量注销（模块/实体卸载清理），返回移除数。"""
        names = [n for n, s in cls._by_name.items() if s.owner == owner]
        for n in names:
            cls.unregister(n)
        return len(names)

    @classmethod
    def get(cls, name: str) -> Optional[LLMHookSpec]:
        """按名取钩子（enabled 属性为运行期生效值）。"""
        spec = cls._by_name.get(name)
        if spec is None:
            return None
        from .settings import effective_spec
        return effective_spec(spec)

    @classmethod
    def set_enabled(cls, name: str, enabled: bool) -> bool:
        """按名启停钩子（运行期热调，web 面板开关）；返回是否找到该钩子。"""
        if name not in cls._by_name:
            return False
        from agent.config import save_config_value

        from .settings import config_keys
        save_config_value(config_keys(cls._by_name[name])["enabled"], bool(enabled))
        return True

    @classmethod
    def for_event(cls, event: str) -> List[LLMHookSpec]:
        """取某事件下全部钩子（已按 priority 降序，enabled 为生效值）。"""
        specs = cls._by_event.get(event, [])
        if not specs:
            return []
        from .settings import effective_spec
        return [effective_spec(spec) for spec in specs]

    @classmethod
    def for_event_enabled(cls, event: str) -> List[LLMHookSpec]:
        """取某事件下启用中的钩子（dispatcher 触发面）。"""
        return [s for s in cls.for_event(event) if s.enabled]

    @classmethod
    def list_all(cls) -> List[LLMHookSpec]:
        """列出全部已注册钩子（按 event + priority 排序，enabled 为生效值）。"""
        out: List[LLMHookSpec] = []
        for event in sorted(cls._by_event):
            out.extend(cls.for_event(event))
        return out

    @classmethod
    def clear(cls) -> None:
        """清空注册表（测试用）。"""
        for name in list(cls._by_name):
            cls.unregister(name)


def llm_hook(
    name: str,
    event: str,
    *,
    context: str = "transcript",
    mode: str = "async",
    when: Optional[HookWhen] = None,
    tool_tags: Optional[List[str]] = None,
    allow_output_tools: bool = False,
    route_output: bool = True,
    max_iterations: int = 6,
    model: str = "",
    max_concurrent: int = 1,
    cooldown_seconds: float = 0.0,
    debounce_seconds: float = 0.0,
    priority: int = 50,
    owner: str = "",
    description: str = "",
    source: str = "code",
    config_key: str = "",
) -> Callable[[HookHandler], HookHandler]:
    """声明式注册 LLM 钩子的装饰器。

    用法::

        @llm_hook("skill_review", event="after_reply", context="transcript",
                  tool_tags=["skills"], max_iterations=6)
        async def skill_review(ctx: HookContext) -> Optional[str]: ...

    装饰即注册进 HookRegistry（事件白名单校验）；handler 原样返回不影响
    其作为普通协程被测试/复用。
    """
    context_mode = HookContextMode(context)
    # llm_end 高频护栏：强制最小冷却，声明更小值（含 0）也钳到下限，
    # 防"每轮 LLM 后都拉起 LLM 工作"的成本失控
    if event == HOOK_EVENT_LLM_END:
        cooldown_seconds = max(cooldown_seconds, LLM_END_MIN_COOLDOWN_SECONDS)

    def _decorator(fn: HookHandler) -> HookHandler:
        enabled = True
        if config_key:
            # 代码注册的钩子可声明配置键，注册时读配置值作为初始启停
            # （运行期仍可用面板 set_enabled 热调覆盖）
            try:
                from core.config import get_config_bool
                enabled = get_config_bool(config_key, True)
            except Exception:
                enabled = True
        spec = LLMHookSpec(
            name=name, event=event, handler=fn, context=context_mode, when=when,
            mode=HookMode(mode),
            tool_tags=tuple(tool_tags or ()), allow_output_tools=allow_output_tools,
            route_output=route_output,
            max_iterations=max_iterations, model=model,
            max_concurrent=max(1, max_concurrent),
            cooldown_seconds=max(0.0, cooldown_seconds),
            debounce_seconds=max(0.0, debounce_seconds),
            priority=priority, owner=owner, description=description, source=source,
            enabled=enabled, config_key=config_key,
        )
        HookRegistry.register(spec)
        return fn

    return _decorator
