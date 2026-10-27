"""TaskExecutor：执行单个任务（LLM 调用 + 结果存储）。

从 introspection units 的执行逻辑提取，提供统一的任务执行流程。

Model Experience:
- 模型看到：任务指令头部带 [执行时间] 与任务元信息（[任务创建]/[最近更新]/
  [生效截止]，运行开始时生成一次，全程冻结）；extra_note（idle 反思触发原因等）
  追加在任务指令（最后一条消息）尾部；历次执行的时长/状态/产出摘要经
  执行历史落盘，由 list_tasks（概况）与 task_history（明细）只读查询
- 模型看到的终态区分完成、未完成和未知；未确认的工具效果只报告回执，不推断完成。
- token 影响：任务指令增量数十 token；结果入库复用去重判断增加一道长期价值判断，无候选时也判断。
- KV Cache 影响：均位于最后一条消息内——执行时间一次运行内字节冻结、
  extra_note 纯尾部追加，任务稳定前缀（人设+工具+永久记忆）不受影响
"""

from __future__ import annotations

import asyncio
import re
import time
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from agent.memory.memory_types import MemoryEntry, MemoryType
from agent.mind.recall import coordinator
from core.event_bus import EVENT_THINKING_INTROSPECTION, event_bus
from core.log import log
from core.tool_results import extract_error_text

from .model import TaskDefinition, TaskResult, format_task_time

if TYPE_CHECKING:
    from agent.memory.ports import MemoryHost
    from agent.messages import EntityData


def _clean_llm_output(text: str) -> str:
    """清洗 LLM 输出：移除思维链标签和模型特定 XML 标签。"""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    text = re.sub(r"</?(?:minimax|invoke|parameter)[^>]*>", "", text)
    return text.strip()


class TaskExecutor:
    """统一的任务执行器：构建上下文 -> LLM 调用 -> 清洗输出 -> 存储结果。"""

    def __init__(self, mind: Any, memory: MemoryHost) -> None:
        self.mind = mind
        self.memory = memory

    async def run(
        self,
        task: TaskDefinition,
        entity: Optional["EntityData"] = None,
        *,
        temperature: float = 0.7,
        model_id: str = "",
        reasoning_effort: str = "",
        extra_note: str = "",
        trigger: str = "manual",
    ) -> Optional[TaskResult]:
        """执行一个任务；完成与未完成由结果区分，正常无产出返回 None。

        工具已尝试但效果未确认时保留未完成事实，避免自动重放副作用；
        未尝试工具的执行异常原样抛出，交由调度重试。

        model_id 优先级：参数传入 > task.model_id > 默认模型。
        reasoning_effort 优先级：参数传入 > task.reasoning_effort > 全局设置。
        extra_note：追加到任务指令之后的动态备注（如 idle 反思触发原因）——
        位于最后一条消息内，属纯尾部追加，不影响任务稳定前缀的缓存命中。
        trigger：执行触发来源（heartbeat/scheduled/idle/manual/event），随执行历史
        落盘供 Web 与 AI 查询。
        """
        if not task.prompt:
            log(f"任务 [{task.name}] prompt 为空，跳过", "WARNING", tag="任务")
            return None

        if not task.should_run_for_entity(entity is not None):
            log(f"任务 [{task.name}] scope 不匹配 (scope={task.scope.value}, has_entity={entity is not None})", tag="任务")
            return None

        if "type:reflection" in task.tags:
            focus = await self._build_reflection_focus(task)
            if focus:
                extra_note = f"{extra_note}{focus}"

        effective_model = model_id or task.model_id or ""
        effective_effort = reasoning_effort or task.reasoning_effort or ""
        await self._emit("unit_start", task, entity)

        started_at = time.time()
        started_mono = time.monotonic()

        def _record(status: str, preview: str = "", error: str = "") -> None:
            from .history import record_execution
            record_execution(
                task.name,
                started_at=started_at,
                duration_ms=int((time.monotonic() - started_mono) * 1000),
                status=status, trigger=trigger,
                preview=preview, error=error,
            )

        completion: Dict[str, Any] = {}

        async def incomplete(detail: str, reason: str, progress: str = "") -> TaskResult:
            content = (f"任务 [{task.name}] 未确认完成：{detail}。已有操作可能生效，"
                       "请先核验实际结果，再基于已有进展续做，不要从头重试。"
                       + (f"\n[本轮进展，未验收]\n{progress}" if progress else ""))
            _record("incomplete", preview=content, error=detail)
            await self._emit("unit_end", task, entity, has_output=True, completed=False,
                             completion_reason=reason, content_preview=content[:300])
            return TaskResult(task_name=task.name, content=content, memory_type=task.memory_type,
                              source=task.source or task.name, tags=list(task.tags), importance=task.importance,
                              completed_reason=reason)

        try:
            # 本任务的工具调用计数经 completion 消息链归因——全局计数器会被
            # 并发的在途回复/钩子/其他后台任务污染，误判无产出任务为成功
            content = await self._execute_llm(
                task, entity, temperature, effective_model, effective_effort,
                extra_note=extra_note, completion=completion, trigger=trigger,
            )
            reason = str(completion.get("reason") or "unknown")
            tool_results = [m for m in completion.get("messages", [])
                            if isinstance(m, dict) and m.get("role") == "tool"]
            failures = [extract_error_text(m.get("content", "")) for m in tool_results]
            unconfirmed = reason != "completed" or (not content and any(failures))
            synthesized_tool_result = not bool(content) and bool(tool_results)
            if unconfirmed:
                detail = f"执行终态为 {reason}" if reason != "completed" else "缺少总结且存在失败或未知的工具回执"
                return await incomplete(detail, reason if reason != "completed" else "failed", content)
            if not content:
                if tool_results:
                    content = (f"任务 [{task.name}] 本轮有 {len(tool_results)} 条工具回执，无文本总结；"
                               "具体操作效果需结合回执核验。")
                else:
                    log(f"任务 [{task.name}] 无产出", tag="任务")
                    _record("no_output")
                    await self._emit("unit_end", task, entity, has_output=False)
                    return None

            for kw in task.null_keywords:
                if kw in content:
                    log(f"任务 [{task.name}] 匹配空响应关键词 [{kw}]，跳过", tag="任务")
                    _record("no_output", preview=content)
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
                log(f"任务 [{task.name}] 仅有工具回执，跳过写入记忆", tag="任务")
            else:
                log(f"任务 [{task.name}] 配置为不写入记忆，跳过存储", tag="任务")
            log(f"任务 [{task.name}] 完成: {content[:80]}", tag="任务")
            _record("no_output" if synthesized_tool_result else "success", preview=content)
            await self._emit("unit_end", task, entity, has_output=True, content_preview=content[:300])
            return result

        except asyncio.CancelledError:
            _record("incomplete", error="任务已取消，执行结果未确认，继续前需核验")
            raise
        except Exception as exc:
            if any(m.get("role") == "tool" or m.get("tool_calls") for m in completion.get("messages", [])
                   if isinstance(m, dict)):
                return await incomplete(f"工具调用后执行异常：{type(exc).__name__}", "failed")
            log(f"任务 [{task.name}] 异常: {exc}", "WARNING", tag="任务")
            _record("error", error=str(exc))
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
            "1. 这是内部任务，普通正文不会投递。需要对外通知时可使用 send_message/send_file/send_photo/send_voice，"
            "必须先核实目标频道与会话并核对回执；禁止发送无关内容或泄露用户隐私信息"
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
        completion: Optional[Dict[str, Any]] = None,
        trigger: str = "manual",
    ) -> str:
        """构建消息 -> LLM reflect -> 清洗输出。"""
        conversation_list: List[Dict] = []
        if entity:
            conversation_list = await self.mind.conversation_data.get_conversation_record_by_everything(entity)

        # 任务精简上下文（task_lean_context）：人设+工具+永久记忆+任务指令；
        # 环境便签/召回/状态由任务按系统规则经 recall/get_conversation 按需取回
        from core.config import get_config_bool
        lean = get_config_bool("task_lean_context", True)
        base_messages = await coordinator.prepare_recollection(self.mind, conversation_list, lean=lean)

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

        from core.activity import activity_owner
        with activity_owner(kind="task", label=task.name, owner_id=task.name,
                            trigger=task.trigger_event if trigger == "event" else trigger):
            raw = await self.mind.reflect(
                messages,
                options=options,
                tool_tags=task.tool_tags or None,
                allow_output_tools=task.allow_output_tools,
                completion=completion,
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

    async def _build_reflection_focus(self, task: TaskDefinition) -> str:
        """为反思类任务组装焦点行：近 24h 工具失败、停滞目标、上次反思锚点。

        让反思有据可依而非全凭模型自觉翻料；三类材料各自独立降级为空，
        任一来源故障不影响任务运行。尾部追加进 extra_note，不碰稳定前缀。
        """
        lines: List[str] = []

        try:
            store = self.memory.memory_store
            if store:
                stats = await store.get_tool_error_stats()
                top = [s for s in stats if s.get("unresolved")][:3]
                if top:
                    brief = ", ".join(
                        f"{s['tool_name']}×{s['total']}" for s in top
                    )
                    lines.append(f"近期待治理工具失败: {brief}")
        except Exception as exc:
            log(f"反思焦点-工具失败概况构建失败: {exc}", "DEBUG", tag="任务")

        try:
            from agent.planning.situation import stale_goal_line
            stale = await stale_goal_line()
            if stale:
                lines.append(stale)
        except Exception as exc:
            log(f"反思焦点-目标停滞概况构建失败: {exc}", "DEBUG", tag="任务")

        try:
            store = self.memory.memory_store
            if store:
                recent = await store.list_recent(
                    limit=1, memory_type=MemoryType.EPISODIC, source=task.name,
                )
                if recent:
                    prev = recent[0]
                    lines.append(f"上次反思 #{prev.id}: {prev.content[:100]}")
        except Exception as exc:
            log(f"反思焦点-上次反思锚点构建失败: {exc}", "DEBUG", tag="任务")

        if not lines:
            return ""
        return "\n\n[反思焦点]\n" + "\n".join(lines)

    async def _store_result(self, result: TaskResult) -> None:
        """将任务结果存入 MemoryStore，写入前经两级去重治理（与 memorize 同一管线）。

        第一级规则判重零成本拦截字面重复；命中第二级 LLM 语义裁决
        （store/skip/update/merge），事实演进合并进既有记忆而非新增矛盾记录。
        任务产出先由同次判断确认长期价值；判断失败时仅保留执行历史摘要，避免把运行汇报写成认知。
        """
        store = self.memory.memory_store
        if not store or not result.content.strip():
            return

        from agent.memory import metrics
        from agent.memory.dedup import (
            apply_update,
            gather_dedup_candidates,
            judge_write,
        )
        from agent.memory.embedding import wake_embedding_worker

        content = result.content
        if await store.has_duplicate_content(content, tags=result.tags):
            metrics.incr("write.dedup_rule_skip")
            log(f"任务结果与已有记忆重复，跳过存储: [{result.task_name}]", tag="任务")
            return

        candidates = await gather_dedup_candidates(store, self.memory.embedder, content)
        verdict = await judge_write(content, candidates, tags=result.tags, task_result=True)
        action = verdict.get("action", "store")
        metrics.incr(f"write.dedup_llm_{action}")

        if action == "skip":
            log(f"任务结果不新增长期记忆: [{result.task_name}] {verdict.get('reason', '已有等价记忆')}", tag="任务")
            return
        if action == "update" and verdict.get("target_id"):
            updated = await apply_update(
                store, int(verdict["target_id"]),
                str(verdict.get("content") or content), result.tags,
                actor=f"task:{result.task_name}",
                expected_version=verdict.get("expected_version"),
            )
            if updated is not None:
                wake_embedding_worker()
                log(f"任务结果合并更新到记忆 #{updated.id}: [{result.task_name}]", tag="任务")
                return
        if action == "merge" and verdict.get("target_ids"):
            merge_ids = [int(i) for i in verdict["target_ids"]]
            keep_id = await store.merge_memories(
                merge_ids, str(verdict.get("content") or content),
                actor=f"task:{result.task_name}",
            )
            if keep_id:
                wake_embedding_worker()
                log(f"任务结果合并 {len(merge_ids)} 条为记忆 #{keep_id}: [{result.task_name}]",
                    tag="任务")
                return

        entry = MemoryEntry(
            memory_type=result.memory_type,
            content=content,
            source=result.source,
            tags=result.tags,
            importance=result.importance,
        )
        from agent.memory.reflection_lifecycle import seed_reflection
        seed_reflection(entry)
        await store.add(entry, actor=f"task:{result.task_name}")
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
