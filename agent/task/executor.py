"""TaskExecutor：执行单个任务（LLM 调用 + 结果存储）。

从 introspection units 的执行逻辑提取，提供统一的任务执行流程。
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from agent.memory.memory_types import MemoryEntry, MemoryType

from core.event_bus import event_bus, EVENT_THINKING_INTROSPECTION
from core.log import log

from .model import TaskDefinition, TaskResult

if TYPE_CHECKING:
    from agent.messages import EntityData


def _clean_llm_output(text: str) -> str:
    """清洗 LLM 输出：移除思维链标签和模型特定 XML 标签。"""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    text = re.sub(r"</?(?:minimax|invoke|parameter)[^>]*>", "", text)
    return text.strip()


class TaskExecutor:
    """统一的任务执行器：构建上下文 -> LLM 调用 -> 清洗输出 -> 存储结果。"""

    def __init__(self, mind: Any) -> None:
        self.mind = mind

    async def run(
        self,
        task: TaskDefinition,
        entity: Optional["EntityData"] = None,
        *,
        temperature: float = 0.7,
    ) -> Optional[TaskResult]:
        """执行一个任务，返回结果或 None。"""
        if not task.prompt:
            log(f"任务 [{task.name}] prompt 为空，跳过", "WARNING", tag="任务")
            return None

        if not task.should_run_for_entity(entity is not None):
            log(f"任务 [{task.name}] scope 不匹配 (scope={task.scope.value}, has_entity={entity is not None})", tag="任务")
            return None

        await self._emit("unit_start", task, entity)

        try:
            content = await self._execute_llm(task, entity, temperature)
            if not content:
                log(f"任务 [{task.name}] 无产出", tag="任务")
                await self._emit("unit_end", task, entity, has_output=False)
                return None

            for kw in task.null_keywords:
                if kw in content:
                    log(f"任务 [{task.name}] 匹配空响应关键词 [{kw}]，跳过", tag="任务")
                    await self._emit("unit_end", task, entity, has_output=False)
                    return None

            result = TaskResult(
                task_name=task.name,
                content=content,
                memory_type=task.memory_type,
                source=task.source or task.name,
                tags=list(task.tags),
                importance=task.importance,
            )

            await self._store_result(result)
            log(f"任务 [{task.name}] 完成: {content[:80]}", tag="任务")
            await self._emit("unit_end", task, entity, has_output=True, content_preview=content[:300])
            return result

        except Exception as exc:
            log(f"任务 [{task.name}] 异常: {exc}", "WARNING", tag="任务")
            await self._emit("unit_error", task, entity, error=str(exc))
            return None

    async def _execute_llm(
        self,
        task: TaskDefinition,
        entity: Optional["EntityData"],
        temperature: float,
    ) -> str:
        """构建消息 -> LLM reflect -> 清洗输出。"""
        conversation_list: List[Dict[str, Any]] = []
        if entity:
            conversation_list = await self.mind.get_conversation(entity)

        base_messages = await self.mind.get_recollection(conversation_list)
        prompt_msg: Dict[str, str] = {
            "role": "user",
            "content": f"[系统任务 - {task.name}]\n{task.prompt}",
        }
        messages = list(base_messages) + [prompt_msg]

        raw = await self.mind.reflect(
            messages,
            options={"temperature": temperature},
            tool_tags=task.tool_tags or None,
        )
        return _clean_llm_output(raw)

    async def _store_result(self, result: TaskResult) -> None:
        """将任务结果存入 MemoryStore。"""
        if not self.mind.memory_store or not result.content.strip():
            return

        if result.memory_type == MemoryType.REFLECTION:
            if await self.mind.memory_store.has_similar_content(result.content):
                log(f"任务结果与已有记忆高度相似，跳过存储: [{result.task_name}]", tag="任务")
                return

        entry = MemoryEntry(
            memory_type=result.memory_type,
            content=result.content,
            source=result.source,
            tags=result.tags,
            importance=result.importance,
        )
        if self.mind.embedder.available:
            entry.embedding = await self.mind.embedder.embed_one(result.content)
        await self.mind.memory_store.add(entry)
        log(f"任务结果已存储: [{result.task_name}] {result.source}", tag="任务")

    @staticmethod
    async def _emit(
        stage: str,
        task: TaskDefinition,
        entity: Optional["EntityData"] = None,
        **extra: Any,
    ) -> None:
        desc = entity.get_entity_desc() if entity else "全局"
        await event_bus.emit(EVENT_THINKING_INTROSPECTION, {
            "stage": stage,
            "unit": task.name,
            "scope": task.scope.value,
            "entity": desc,
            **extra,
        })
