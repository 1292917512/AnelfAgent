"""反思/任务单元基础定义：抽象基类、上下文、结果、作用域和执行模式枚举。"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from agent.memory.memory_types import MemoryType

if TYPE_CHECKING:
    from agent.messages import EntityData
    from .config import IntrospectionConfig


class UnitScope(str, Enum):
    """单元的作用域。"""

    ENTITY = "entity"
    """需要具体实体才能执行。"""

    GLOBAL = "global"
    """仅在无实体的全局反思中执行。"""

    ANY = "any"
    """有无实体均可执行。"""


class UnitMode(str, Enum):
    """单元的执行模式。"""

    REFLECT = "reflect"
    """反思模式：心跳自动触发，按 scope 过滤，受间隔限制。"""

    TASK = "task"
    """任务模式：按名称指定执行，不受间隔限制，可配置专属工具集。"""


@dataclass
class IntrospectionContext:
    """传递给每个单元的执行上下文。"""

    mind: Any
    entity: Optional["EntityData"]
    conversation_list: List[Dict[str, Any]]
    config: "IntrospectionConfig"
    memory_warnings_checked: bool = False
    active_channel_scopes: Dict[str, List[str]] = field(default_factory=dict)


@dataclass
class IntrospectionResult:
    """单个单元的产出。"""

    unit_name: str
    content: str
    memory_type: MemoryType
    source: str
    tags: List[str] = field(default_factory=list)
    importance: float = 0.7


class IntrospectionUnit(ABC):
    """反思/任务单元抽象基类。

    子类需设置 name / scope / default_prompt 属性，并实现 execute 方法。
    mode 决定执行模式：REFLECT（自动反思）或 TASK（按名指定执行）。
    tool_tags 允许任务指定专属工具集（空列表使用默认工具）。
    """

    name: str
    scope: UnitScope
    mode: UnitMode = UnitMode.REFLECT
    description: str = ""
    default_prompt: str = ""
    enabled: bool = True
    tool_tags: List[str] = []

    def get_prompt(self, ctx: IntrospectionContext) -> str:
        """获取最终执行的 prompt（配置覆盖 > 类默认）。"""
        return ctx.config.get_unit(self.name).prompt or self.default_prompt

    def should_run(self, ctx: IntrospectionContext) -> bool:
        """判断当前上下文是否应执行该单元（仅用于自动反思过滤）。"""
        if not self.enabled:
            return False
        if self.mode == UnitMode.TASK:
            return False
        if self.scope == UnitScope.ENTITY and ctx.entity is None:
            return False
        if self.scope == UnitScope.GLOBAL and ctx.entity is not None:
            return False
        return True

    async def _emit_phase(self, phase: str, **extra: Any) -> None:
        """向思维追踪器发射单元执行阶段事件。"""
        from core.event_bus import event_bus, EVENT_THINKING_INTROSPECTION
        await event_bus.emit(EVENT_THINKING_INTROSPECTION, {
            "stage": "unit_phase",
            "unit": self.name,
            "phase": phase,
            **extra,
        })

    @abstractmethod
    async def execute(self, ctx: IntrospectionContext) -> Optional[IntrospectionResult]:
        """执行逻辑，返回结果或 None（无产出时）。"""
        ...

    async def _run_llm_reflection(
        self,
        ctx: IntrospectionContext,
        prompt: str,
        conversation_list: Optional[List[Dict[str, Any]]] = None,
    ) -> str:
        """标准化 LLM 反思执行流程：构建消息 -> reflect -> 清洗输出。"""
        await self._emit_phase("context_build")
        conv = conversation_list if conversation_list is not None else ctx.conversation_list
        base_messages = await ctx.mind.get_recollection(conv)
        prompt_msg: Dict[str, str] = {
            "role": "user",
            "content": f"[系统反思 - {self.name}]\n{prompt}",
        }
        messages = list(base_messages) + [prompt_msg]

        await self._emit_phase("llm_start")
        raw = await ctx.mind.reflect(
            messages,
            options={"temperature": ctx.config.analysis_temperature},
            tool_tags=self.tool_tags or None,
        )
        content = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL)
        content = re.sub(r"</?(?:minimax|invoke|parameter)[^>]*>", "", content).strip()

        await self._emit_phase(
            "llm_end",
            content_preview=content[:120] if content else "（无产出）",
        )
        return content
