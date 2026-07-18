"""HeartbeatEngine：心跳调度核心 — 周期性任务分发与内置维护。

每次心跳（tick）：
1. 内置维护（EntityAnalysis / MemoryHealth / 日志合并 / 实体计数持久化）
2. 遍历 task_schedules 检查是否到达触发条件
3. 持久化计数器

由 Mind 定时器周期性调用，不自行管理定时器。
"""

from __future__ import annotations

import re
import time
from datetime import datetime
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from agent.memory.memory_types import MemoryEntry, MemoryType
from agent.task.model import TaskDefinition, TaskResult
from agent.task.registry import TaskRegistry
from agent.task.executor import TaskExecutor

from core.event_bus import event_bus, EVENT_THINKING_INTROSPECTION, EVENT_THINKING_SESSION_START, EVENT_THINKING_SESSION_END
from core.log import log

from .config import HeartbeatConfig, ScheduleMode, get_heartbeat_config
from . import log as hb_log

if TYPE_CHECKING:
    from agent.messages import EntityData
    from agent.mind.mind import Mind

_ENTITY_ANALYSIS_PROMPT = (
    "请对 {entity} 进行画像分析并输出结构化 Markdown 总结。\n\n"
    "## 分析要求\n"
    "1. 仔细阅读对话历史和已有画像（如有），提取关键信息\n"
    "2. 可使用工具辅助分析（recall 检索相关记忆、get_conversation 查看完整对话）\n"
    "3. **增量更新**：保留已有画像中仍然准确的信息，补充新发现，修正过时内容\n"
    "4. 输出将覆盖旧画像，务必确保完整性——不要遗漏旧画像中仍有效的信息\n\n"
    "## 用户画像模板（当 {entity} 为用户时使用）\n"
    "```\n"
    "## 基本信息\n- 名称/昵称：（从对话中提取的称呼）\n- 身份标识：{entity}\n"
    "## 性格印象\n（说话风格、性格特点、行为模式）\n"
    "## 兴趣爱好\n（话题偏好、关注领域）\n"
    "## 关系与互动风格\n（与我的关系、互动特点、称呼习惯）\n"
    "## 重要事件\n（值得记住的对话内容、承诺、约定）\n"
    "## 注意事项\n（需要特别留意的偏好或禁忌）\n```\n\n"
    "## 群组画像模板（当 {entity} 为群组时使用）\n"
    "```\n"
    "## 群组概况\n- 群组标识：{entity}\n- 群组定位/主题：\n"
    "## 活跃成员\n（列出主要成员及其特点）\n"
    "## 群组氛围\n（交流风格、群内文化）\n"
    "## 重要事件\n（群内发生的关键事件）\n"
    "## 注意事项\n（群规、敏感话题等）\n```\n\n"
    "完成工具操作后，直接输出画像内容（纯 Markdown，不要包裹在代码块中）。"
)


def _clean_llm(text: str) -> str:
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    text = re.sub(r"</?(?:minimax|invoke|parameter)[^>]*>", "", text)
    return text.strip()


class HeartbeatEngine:
    """心跳调度引擎。"""

    def __init__(self, mind: "Mind") -> None:
        self.mind = mind
        self.config = get_heartbeat_config()
        self.task_registry = TaskRegistry()
        self.executor = TaskExecutor(mind)
        self._total_ticks: int = 0

    @property
    def total_ticks(self) -> int:
        return self._total_ticks

    def reload(self) -> None:
        """热重载任务注册表和心跳配置。"""
        self.task_registry.reload()
        from .config import reload_heartbeat_config
        self.config = reload_heartbeat_config()

    # ------------------------------------------------------------------
    # 心跳主循环
    # ------------------------------------------------------------------

    async def tick(self) -> List[str]:
        """单次心跳 — 返回本次执行的任务名列表。

        每次心跳只执行一个到期任务（避免长任务阻塞），
        所有计数器无论是否执行都会递增并持久化。
        """
        self._total_ticks += 1
        executed: List[str] = []

        await self._run_maintenance()

        pending_task: Optional[TaskDefinition] = None
        pending_schedule_idx: int = -1

        for idx, schedule in enumerate(self.config.task_schedules):
            task = self.task_registry.get(schedule.task_name)
            if not task or not task.enabled:
                continue

            if schedule.mode == ScheduleMode.HEARTBEAT:
                schedule.beat_count += 1
                if schedule.beat_count >= schedule.every_n_beats and pending_task is None:
                    pending_task = task
                    pending_schedule_idx = idx

            elif schedule.mode == ScheduleMode.SCHEDULED:
                if self._is_scheduled_now(schedule.schedule_times, schedule.last_run_date) and pending_task is None:
                    pending_task = task
                    pending_schedule_idx = idx

        if pending_task is not None and pending_schedule_idx >= 0:
            schedule = self.config.task_schedules[pending_schedule_idx]
            if schedule.mode == ScheduleMode.HEARTBEAT:
                schedule.beat_count = 0
            elif schedule.mode == ScheduleMode.SCHEDULED:
                schedule.last_run_date = datetime.now().strftime("%Y-%m-%d")

            entity = await self._pop_analysis_entity() if pending_task.scope.value == "entity" else None
            await self.executor.run(
                pending_task, entity,
                temperature=self.config.analysis_temperature,
                model_id=schedule.model_id,
                reasoning_effort=schedule.reasoning_effort,
            )
            executed.append(pending_task.name)

        self.config.save()
        return executed

    # ------------------------------------------------------------------
    # 手动触发任务
    # ------------------------------------------------------------------

    async def run_task(self, task_name: str) -> Optional[str]:
        """按名称执行指定任务，返回产出内容或 None。"""
        task = self.task_registry.get(task_name)
        if not task:
            log(f"任务 [{task_name}] 不存在", "WARNING", tag="心跳")
            return None
        if not task.enabled:
            log(f"任务 [{task_name}] 已禁用", "WARNING", tag="心跳")
            return None

        log(f"手动执行任务: {task_name}", tag="心跳")

        await event_bus.emit(EVENT_THINKING_SESSION_START, {
            "is_heartbeat": True, "is_introspection": True, "entity": "任务执行",
        })
        try:
            entity = await self._pop_analysis_entity() if task.scope.value == "entity" else None
            result = await self.executor.run(
                task, entity, temperature=self.config.analysis_temperature,
            )
            return result.content if result else None
        finally:
            await event_bus.emit(EVENT_THINKING_SESSION_END, {"reason": "task_completed"})

    # ------------------------------------------------------------------
    # 内置维护
    # ------------------------------------------------------------------

    async def _run_maintenance(self) -> None:
        """内置维护步骤（不可由用户配置为任务）。"""
        try:
            from agent.memory.notes import consolidate_heartbeat
            consolidate_heartbeat()
        except Exception as e:
            log(f"心跳日志合并失败: {e}", "DEBUG", tag="心跳")

        try:
            saved = await self.mind.everything_data.save_all_entity_counters()
            if saved:
                log(f"心跳持久化实体计数: {saved} 个", "DEBUG", tag="心跳")
        except Exception as e:
            log(f"实体计数持久化失败: {e}", "DEBUG", tag="心跳")

        memory_warnings = await self._check_memory_health()
        for warn in memory_warnings:
            hb_log.append_entry(f"[记忆预警] {warn}")

        # 记忆整理：遗忘低价值记忆 + 类型上限 + 高相似合并 + cognee 同步（人脑"睡眠整理"）
        try:
            if self.mind.memory_store:
                from agent.memory.consolidator import MemoryConsolidator
                report = await MemoryConsolidator(self.mind.memory_store).consolidate()
                for line in report.to_log_lines():
                    hb_log.append_entry(f"[记忆整理] {line}")
        except Exception as e:
            log(f"记忆整理失败: {e}", "DEBUG", tag="心跳")

        # 技能策展：长期未用技能自动降级/归档（确定性状态机，无 LLM）
        try:
            curator = getattr(self.mind, "skill_curator", None)
            if curator is not None:
                report = curator.apply_automatic_transitions()
                if report["staled"] or report["archived"]:
                    hb_log.append_entry(
                        f"[技能策展] 降级 {len(report['staled'])} 个，"
                        f"归档 {len(report['archived'])} 个"
                    )
        except Exception as e:
            log(f"技能策展失败: {e}", "DEBUG", tag="心跳")

        entity = await self._pop_analysis_entity()
        if entity:
            await self._run_entity_analysis(entity)

    async def _check_memory_health(self) -> List[str]:
        """记忆健康检查：纯逻辑检查阈值。"""
        if not self.mind.memory_store:
            return []
        warnings: List[str] = []
        try:
            type_counts = await self.mind.memory_store.get_type_counts()
            entity_count = type_counts.get("entity", 0)
            if entity_count > 5:
                warnings.append(
                    f"实体记忆有 {entity_count} 条（阈值 5），"
                    "建议使用 memory_deep_search 查看并用 merge_memories 合并"
                )
            reflection_count = type_counts.get("reflection", 0)
            if reflection_count > 10:
                warnings.append(
                    f"反思记忆有 {reflection_count} 条（阈值 10），"
                    "建议使用 memory_deep_search 查看并用 merge_memories 合并"
                )
            for mem_type, count in type_counts.items():
                if mem_type in ("entity", "reflection"):
                    continue
                if count > 200:
                    warnings.append(f"{mem_type} 记忆有 {count} 条（阈值 200），建议整理")
        except Exception as exc:
            log(f"记忆阈值检查异常: {exc}", "WARNING", tag="心跳")
        if warnings:
            log(f"记忆阈值预警: {len(warnings)} 条", tag="心跳")
        return warnings

    async def _run_entity_analysis(self, entity: "EntityData") -> Optional[TaskResult]:
        """内置实体画像分析。"""
        from agent.messages import MessageAssistant

        desc = entity.get_entity_desc()
        log(f"实体画像分析: {desc}", tag="心跳")

        min_conv = self.config.min_conversations_for_analysis
        conversation = await self.mind.get_conversation(entity)
        if len(conversation) < min_conv:
            log(f"对话不足: {desc} ({len(conversation)}/{min_conv})", tag="心跳")
            return None

        prompt = _ENTITY_ANALYSIS_PROMPT.replace("{entity}", desc)

        user_query_entity = MessageAssistant(uid=entity.uid or 0)
        user_conv = await self.mind.get_conversation(user_query_entity)
        combined = conversation + user_conv

        alias_convs = await self._collect_alias_conversations(entity)
        if alias_convs:
            combined = combined + alias_convs

        base_messages = await self.mind.get_recollection(combined)
        personality_desc = entity.get_personality_desc()
        analysis_messages = list(base_messages)
        if personality_desc:
            analysis_messages.append(personality_desc)
        analysis_messages.append({
            "role": "user",
            "content": f"[系统任务 - entity_analysis]\n{prompt}",
        })

        raw = await self.mind.reflect(
            analysis_messages,
            options={"temperature": self.config.analysis_temperature},
        )
        content = _clean_llm(raw)
        if not content:
            return None

        entity.set_personality(content)
        await self.mind.everything_data.save_entity_personality(entity)
        log(f"实体画像更新: {desc} -> {content[:80]}", tag="心跳")

        entity_id = str(entity.uid or entity.group_id)
        source = f"entity_{entity_id}"
        scope_tag = f"user:{entity_id}" if entity.uid else f"group:{entity_id}"

        if self.mind.memory_store:
            old_entries = await self.mind.memory_store.list_recent(
                limit=5, memory_type=MemoryType.ENTITY, source=source,
            )
            for old in old_entries:
                if old.id:
                    await self.mind.memory_store.delete(old.id)

            entry = MemoryEntry(
                memory_type=MemoryType.ENTITY,
                content=content,
                source=source,
                tags=[scope_tag, "type:profile"],
                importance=0.8,
            )
            if self.mind.embedder.available:
                entry.embedding = await self.mind.embedder.embed_one(content)
            await self.mind.memory_store.add(entry)

        hb_log.append_entry(f"[entity_analysis] {desc}: {content[:100]}")
        return TaskResult(
            task_name="entity_analysis",
            content=content,
            memory_type=MemoryType.ENTITY,
            source=source,
            tags=[scope_tag, "type:profile"],
            importance=0.8,
        )

    async def _pop_analysis_entity(self) -> Optional["EntityData"]:
        try:
            return await self.mind.pfc.pop_analysis_task()
        except Exception:
            return None

    async def _collect_alias_conversations(self, entity: "EntityData") -> List[Dict[str, Any]]:
        """收集所有 alias 关联身份的对话记录。"""
        try:
            from agent.messages import MessageAssistant
            sqlite = self.mind.everything_data.router.sqlite
            scope_type = "user" if entity.uid and entity.uid not in (0, "0") else "group"
            scope_id = str(entity.uid) if scope_type == "user" else str(entity.group_id)
            primary = await sqlite.resolve_alias(scope_type, scope_id)
            p_type, p_id = primary if primary else (scope_type, scope_id)
            aliases = await sqlite.get_aliases_for_primary(p_type, p_id)
            all_ids = [(p_type, p_id)] + [(a["scope_type"], a["scope_id"]) for a in aliases]
            current = (scope_type, scope_id)
            extra: List[Dict[str, Any]] = []
            for id_type, id_id in all_ids:
                if (id_type, id_id) == current:
                    continue
                alias_entity = MessageAssistant(
                    uid=id_id if id_type == "user" else 0,
                    group_id=id_id if id_type == "group" else 0,
                )
                conv = await self.mind.get_conversation(alias_entity)
                if conv:
                    extra.extend(conv)
            return extra
        except Exception as exc:
            log(f"alias 对话收集失败: {exc}", "WARNING", tag="心跳")
            return []

    @staticmethod
    def _is_scheduled_now(times: List[str], last_run_date: str) -> bool:
        """检查当前时间是否匹配调度时间（且今天未执行过）。"""
        now = datetime.now()
        today = now.strftime("%Y-%m-%d")
        if last_run_date == today:
            return False
        current_hm = now.strftime("%H:%M")
        for t in times:
            try:
                if current_hm >= t:
                    return True
            except (ValueError, TypeError):
                continue
        return False

    def get_status(self) -> Dict[str, Any]:
        """返回心跳引擎运行状态。"""
        return {
            "enabled": self.config.enabled,
            "interval_seconds": self.config.interval_seconds,
            "total_ticks": self._total_ticks,
            "task_count": len(self.task_registry.list_all()),
            "schedule_count": len(self.config.task_schedules),
            "schedules": [
                {
                    **s.to_dict(),
                    "task_exists": self.task_registry.get(s.task_name) is not None,
                    "task_enabled": (self.task_registry.get(s.task_name) or TaskDefinition(name="")).enabled,
                    "model_id": s.model_id,
                }
                for s in self.config.task_schedules
            ],
        }
