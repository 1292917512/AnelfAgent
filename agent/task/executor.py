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
        model_id: str = "",
        reasoning_effort: str = "",
    ) -> Optional[TaskResult]:
        """执行一个任务，返回结果或 None。

        model_id 优先级：参数传入 > task.model_id > 默认模型。
        reasoning_effort 优先级：参数传入 > task.reasoning_effort > 全局设置。
        """
        if not task.prompt:
            log(f"任务 [{task.name}] prompt 为空，跳过", "WARNING", tag="任务")
            return None

        if not task.should_run_for_entity(entity is not None):
            log(f"任务 [{task.name}] scope 不匹配 (scope={task.scope.value}, has_entity={entity is not None})", tag="任务")
            return None

        effective_model = model_id or task.model_id or ""
        effective_effort = reasoning_effort or task.reasoning_effort or ""
        await self._emit("unit_start", task, entity)

        try:
            tool_hits_before = self.mind.pfc.get_tool_use_total()
            content = await self._execute_llm(task, entity, temperature, effective_model, effective_effort)
            tool_hits_after = self.mind.pfc.get_tool_use_total()
            synthesized_tool_result = False
            if not content:
                if tool_hits_after > tool_hits_before:
                    synthesized_tool_result = True
                    content = (
                        f"任务 [{task.name}] 已执行 {tool_hits_after - tool_hits_before} 次工具调用，"
                        "无文本产出（工具副作用已完成）"
                    )
                    log(f"任务 [{task.name}] 工具执行完成（无文本产出）", tag="任务")
                else:
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

            if task.save_result_to_memory and not synthesized_tool_result:
                await self._store_result(result)
            elif synthesized_tool_result:
                log(f"任务 [{task.name}] 为工具副作用完成态，跳过写入记忆", tag="任务")
            else:
                log(f"任务 [{task.name}] 配置为不写入记忆，跳过存储", tag="任务")
            log(f"任务 [{task.name}] 完成: {content[:80]}", tag="任务")
            await self._emit("unit_end", task, entity, has_output=True, content_preview=content[:300])
            return result

        except Exception as exc:
            log(f"任务 [{task.name}] 异常: {exc}", "WARNING", tag="任务")
            await self._emit("unit_error", task, entity, error=str(exc))
            return None

    @staticmethod
    def _build_task_suffix(allow_output_tools: bool) -> str:
        """按任务配置构建系统规则后缀。"""
        rule_1 = (
            "1. 这是内部任务，仅可在任务明确要求时使用 send_message/send_file/send_photo/send_voice 外发结果，"
            "禁止发送无关内容，严禁泄露用户隐私信息"
            if allow_output_tools
            else "1. 这是内部任务，严禁向任何频道/用户发送消息，严禁泄露任何用户隐私信息"
        )
        return (
            "\n\n[系统规则]\n"
            f"{rule_1}\n"
            "2. 要了解会话内容必须用 get_conversation 实际读取消息，而非只看 scope 列表\n"
            "3. 操作前先用 recall/list_goals 检查已有记忆和目标，避免重复记录和重复提问\n"
            "4. 完成后调用 log_to_heartbeat 记录操作总结，然后 end_reply 结束"
        )

    async def _execute_llm(
        self,
        task: TaskDefinition,
        entity: Optional["EntityData"],
        temperature: float,
        model_id: str = "",
        reasoning_effort: str = "",
    ) -> str:
        """构建消息 -> LLM reflect -> 清洗输出。"""
        conversation_list: List[Dict[str, Any]] = []
        if entity:
            conversation_list = await self.mind.get_conversation(entity)

        base_messages = await self.mind.get_recollection(conversation_list)
        prompt_msg: Dict[str, str] = {
            "role": "user",
            "content": (
                f"[系统任务 - {task.name}]\n"
                f"{task.prompt}{self._build_task_suffix(task.allow_output_tools)}"
            ),
        }
        messages = list(base_messages) + [prompt_msg]

        options: Dict[str, Any] = {"temperature": temperature}
        if model_id:
            options["_model_id"] = model_id
        if reasoning_effort:
            options["reasoning_effort"] = reasoning_effort

        raw = await self.mind.reflect(
            messages,
            options=options,
            tool_tags=task.tool_tags or None,
            allow_output_tools=task.allow_output_tools,
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
        await self.mind.memory_store.add(entry)
        from agent.memory.embedding_worker import wake_embedding_worker
        wake_embedding_worker()
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
