"""TaskExecutor：执行单个任务（LLM 调用 + 结果存储）。

从 introspection units 的执行逻辑提取，提供统一的任务执行流程。

Model Experience:
- 模型看到：任务指令头部带 [执行时间] 与任务元信息（[任务创建]/[最近更新]/
  [生效截止]，运行开始时生成一次，全程冻结）；extra_note（idle 反思触发原因等）
  追加在任务指令（最后一条消息）尾部
- token 影响：极小（数十 token）
- KV Cache 影响：均位于最后一条消息内——执行时间一次运行内字节冻结、
  extra_note 纯尾部追加，任务稳定前缀（人设+工具+永久记忆）不受影响
"""

from __future__ import annotations

import re
import time
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from agent.memory.memory_types import MemoryEntry, MemoryType
from core.event_bus import EVENT_THINKING_INTROSPECTION, event_bus
from core.log import log

from .model import TaskDefinition, TaskResult, format_task_time

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
        extra_note: str = "",
    ) -> Optional[TaskResult]:
        """执行一个任务，返回结果；正常无产出返回 None，执行异常则抛出。

        调用方据此区分「执行成功但无产出」（可推进调度标记）与「执行失败」
        （应保留标记以便重试，实现 at-least-once）。

        model_id 优先级：参数传入 > task.model_id > 默认模型。
        reasoning_effort 优先级：参数传入 > task.reasoning_effort > 全局设置。
        extra_note：追加到任务指令之后的动态备注（如 idle 反思触发原因）——
        位于最后一条消息内，属纯尾部追加，不影响任务稳定前缀的缓存命中。
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
            content = await self._execute_llm(
                task, entity, temperature, effective_model, effective_effort,
                extra_note=extra_note,
            )
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
            raise

    @staticmethod
    def _build_task_meta_lines(task: TaskDefinition) -> str:
        """任务元信息行（创建/更新/生效截止），供任务自检判断新旧与有效性。"""
        lines: List[str] = []
        created = format_task_time(task.created_at)
        if created:
            lines.append(f"[任务创建] {created}")
        updated = format_task_time(task.updated_at)
        if updated:
            lines.append(f"[最近更新] {updated}")
        if task.expires_at:
            lines.append(f"[生效截止] {task.expires_at}（到期自动停用，可用 update_task 延期）")
        return "\n".join(lines) + "\n" if lines else ""

    @staticmethod
    def _build_task_suffix(allow_output_tools: bool, handoff: bool = False) -> str:
        """按任务配置构建系统规则后缀。"""
        rule_1 = (
            "1. 这是内部任务，仅可在任务明确要求时使用 send_message/send_file/send_photo/send_voice 外发结果，"
            "禁止发送无关内容，严禁泄露用户隐私信息"
            if allow_output_tools
            else "1. 这是内部任务，严禁向任何频道/用户发送消息，严禁泄露任何用户隐私信息"
        )
        rules = [
            "\n\n[系统规则]\n",
            f"{rule_1}\n",
            "2. 要了解会话内容必须用 get_conversation 实际读取消息，而非只看 scope 列表\n",
            "3. 操作前先用 recall/list_goals 检查已有记忆和目标，避免重复记录和重复提问\n",
            "4. 完成后调用 log_to_heartbeat 记录操作总结，然后 end_reply 结束\n",
            "5. 任务自检：结合头部元信息判断本任务是否仍然有效——目标已达成/被新事实推翻/"
            "指令需要修订时用 update_task 更新（可改 prompt 或用 expires_at 延期）；"
            "确认彻底废弃用 delete_task 删除；到期任务系统会自动停用，无需手动处理",
        ]
        if handoff:
            # 长任务交接：输出末尾追加结构化块，供下次运行确定性接力
            # （策略进指令而非全局人设——只有 handoff 任务看到）
            rules.append(
                "\n6. 本任务为多轮接力任务：在输出最末尾追加一段以 \"# HANDOFF\" 行起始的"
                "交接块（JSON：{\"summary\": 本轮进展, \"next_steps\": [下一步…], "
                "\"blocker\": 阻塞或null}），供下次运行接续；其余输出不要包含该块"
            )
        return "".join(rules)

    async def _execute_llm(
        self,
        task: TaskDefinition,
        entity: Optional["EntityData"],
        temperature: float,
        model_id: str = "",
        reasoning_effort: str = "",
        *,
        extra_note: str = "",
    ) -> str:
        """构建消息 -> LLM reflect -> 清洗输出。"""
        conversation_list: List[Dict] = []
        if entity:
            conversation_list = await self.mind.get_conversation(entity)

        # 任务精简上下文（task_lean_context）：人设+工具+永久记忆+任务指令；
        # 环境便签/召回/状态由任务按系统规则经 recall/get_conversation 按需取回
        from core.config import get_config_bool
        lean = get_config_bool("task_lean_context", True)
        base_messages = await self.mind.get_recollection(conversation_list, lean=lean)

        # 长任务交接注入：上次运行留下的 "# HANDOFF" 块（确定性接力，recall 之外的信息通道）
        handoff_prefix = ""
        if task.handoff:
            from agent.task.handoff import load_handoff
            prev = load_handoff(task.name)
            if prev:
                handoff_prefix = f"\n\n[上次交接]\n{prev}\n（请在此基础上继续，工作区文件是权威状态）"
        prompt_msg: Dict[str, str] = {
            "role": "user",
            "content": (
                f"[系统任务 - {task.name}]\n"
                f"[执行时间] {time.strftime('%Y-%m-%d %H:%M')}\n"
                f"{self._build_task_meta_lines(task)}"
                f"{task.prompt}{handoff_prefix}{extra_note}"
                f"{self._build_task_suffix(task.allow_output_tools, handoff=task.handoff)}"
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
        cleaned = _clean_llm_output(raw)
        if not task.handoff:
            return cleaned
        # 提取交接块：干净输出走原流程，handoff 持久化供下次注入
        from agent.task.handoff import extract_handoff, save_handoff
        clean_output, handoff_text = extract_handoff(cleaned)
        if handoff_text:
            save_handoff(task.name, handoff_text)
        return clean_output

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
        from agent.memory.embedding import wake_embedding_worker
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
