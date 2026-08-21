"""HeartbeatEngine：心跳调度核心 — 周期性任务分发与内置维护。

每次心跳（tick）：
1. 内置维护（EntityAnalysis / MemoryHealth / 日志合并 / 实体计数持久化）
2. 遍历 task_schedules 检查是否到达触发条件
3. 持久化计数器

由 Mind 定时器周期性调用，不自行管理定时器。

idle 空闲调度（ScheduleMode.IDLE）：计数维度是"距上次思考的连续空闲心跳数"
（mind.last_activity_ts 锚点，任何 reply/reflect 活动清零，含 idle 任务自身），
仅在本 tick 无确定性到期任务时评估触发——反思与自由活动收敛到空闲窗口，
不打断对话节奏。同任务排队经 _task_inflight 去重（排队里同一种任务只允许一条）。

Model Experience:
- 模型看到：idle 任务的 extra_note（反思触发原因）追加在任务指令尾部；
  心跳日志新增"反思已登记"条目（volatile 态势，不进前缀层）
- token 影响：反思从对话中段延迟到空闲批量执行，总体更省
- KV Cache 影响：零——无 prompt 分层变更，extra_note 为尾部追加
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from agent.memory.memory_types import MemoryEntry, MemoryType
from agent.task.executor import TaskExecutor
from agent.task.executor import _clean_llm_output as _clean_llm
from agent.task.model import TaskDefinition, TaskResult
from agent.task.registry import TaskRegistry
from core.log import log
from core.trace_session import thinking_session

from . import log as hb_log
from .config import ScheduleMode, get_heartbeat_config

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
    "## 关系与互动风格\n（与我的关系、互动特点、称呼习惯；具体的人物关系事实由关系图谱维护，"
    "此处只写互动风格，不罗列关系清单）\n"
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


_EVENT_DISTILL_PROMPT = (
    "以下是一天的事件便签内容，即将超过保留期被归档删除。\n"
    "请提取其中值得长期保留的事实、事件、约定、里程碑，"
    "以要点列表输出（每行一个要点，简洁具体，保留关键时间与对象）。\n"
    "若没有长期保留价值的内容，只输出一个字：无。不要输出任何其他解释。\n\n"
    "【日期】{date}\n【便签内容】\n{content}"
)

# 单次提炼送入 LLM 的便签内容上限（超出截断，避免上下文膨胀）
_DISTILL_MAX_CHARS = 8000

# 每次心跳维护最多归档的过期便签数（避免阻塞心跳）
_ARCHIVE_MAX_PER_TICK = 2


class HeartbeatEngine:
    """心跳调度引擎。"""

    _MAX_TASK_FAILURES = 3  # 任务连续失败上限，达到后放弃本轮调度标记
    _MAX_ANALYSIS_ATTEMPTS = 3  # 实体画像分析连续未执行上限，达到后放弃该实体

    def __init__(self, mind: "Mind") -> None:
        self.mind = mind
        self.config = get_heartbeat_config()
        self.task_registry = TaskRegistry()
        self.executor = TaskExecutor(mind)
        self._total_ticks: int = 0
        self._tick_lock = asyncio.Lock()
        self._task_failures: Dict[str, int] = {}
        self._analysis_attempts: Dict[tuple, int] = {}
        self._warned_missing_tasks: set[str] = set()
        # 同任务排队去重：正在执行/排队触发的任务名集合（tick 与手动触发共用，
        # asyncio 单线程下 check-then-set 无竞态），保证排队里同一种任务只有一条
        self._task_inflight: set[str] = set()
        # idle 空闲计数锚点：上次 tick 已消费到的 mind 思考活动时间戳，
        # 大于它说明两次 tick 之间发生过真实思考 → 空闲计数清零
        self._idle_anchor_ts: float = 0.0
        # 待执行反思原因（元决策 REFLECT 延迟到空闲窗口消费；空 = 无）
        self._reflection_pending: str = ""
        # 空闲折叠跟踪：scope → 最近一次见到的最新消息 ts / 连续无新消息心跳数
        self._fold_activity_ts: Dict[str, int] = {}
        self._fold_idle_beats: Dict[str, int] = {}
        self._prune_orphan_schedules()

    def _prune_orphan_schedules(self) -> None:
        """清理任务文件已删除的孤儿调度（任务文件仍在但加载失败的不动，保留 WARN 提示）。"""
        orphans = [
            s.task_name for s in self.config.task_schedules
            if self.task_registry.get(s.task_name) is None
            and not self.task_registry.task_file_exists(s.task_name)
        ]
        if not orphans:
            return
        for name in orphans:
            self.config.remove_schedule(name)
            self._warned_missing_tasks.discard(name)
        self.config.save()
        log(f"已清理 {len(orphans)} 个孤儿心跳调度: {orphans}", tag="心跳")

    @property
    def total_ticks(self) -> int:
        return self._total_ticks

    def reload(self) -> None:
        """热重载任务注册表和心跳配置。"""
        self.task_registry.reload()
        self._warned_missing_tasks.clear()
        from .config import reload_heartbeat_config
        self.config = reload_heartbeat_config()
        self._prune_orphan_schedules()

    # ------------------------------------------------------------------
    # 心跳主循环
    # ------------------------------------------------------------------

    async def tick(self) -> List[str]:
        """单次心跳 — 返回本次执行的任务名列表。

        每次心跳只执行一个到期任务（避免长任务阻塞），
        所有计数器无论是否执行都会递增并持久化。
        使用 asyncio.Lock 保证 Web 手动 tick 与调度 tick 互斥。
        """
        async with self._tick_lock:
            return await self._tick_inner()

    async def _tick_inner(self) -> List[str]:
        """tick 实际逻辑（已持锁）。"""
        self._total_ticks += 1
        executed: List[str] = []

        await self._run_maintenance()

        # 到期定时提醒触发（持久化提醒，含停机期间错过的补触发）
        try:
            from agent.mind.tools.scheduler import check_due_reminders
            await check_due_reminders()
        except Exception as e:
            log(f"定时提醒检查失败: {e}", "DEBUG", tag="心跳")

        pending_task: Optional[TaskDefinition] = None
        pending_schedule_idx: int = -1
        pending_extra_note: str = ""
        # 计数器/标记脏标记：无 heartbeat/idle 模式调度且未执行任务时跳过落盘
        dirty = False

        for idx, schedule in enumerate(self.config.task_schedules):
            task = self.task_registry.get(schedule.task_name)
            if not task:
                # 调度指向不存在的任务定义（如任务文件已删除但调度残留）：
                # 无法执行也不应累积计数，每个任务名只告警一次避免刷屏
                if schedule.task_name not in self._warned_missing_tasks:
                    self._warned_missing_tasks.add(schedule.task_name)
                    log(
                        f"调度 [{schedule.task_name}] 无对应任务定义"
                        "（config/tasks 下缺失），已跳过；"
                        "请在心跳配置中移除该调度或补充任务文件",
                        "WARNING", tag="心跳",
                    )
                continue
            if not task.enabled:
                continue
            # 已在执行/排队中的任务不再重复选取（排队里同一种任务只允许一条）
            if schedule.task_name in self._task_inflight:
                continue

            if schedule.mode == ScheduleMode.HEARTBEAT:
                schedule.beat_count += 1
                dirty = True
                if schedule.beat_count >= schedule.every_n_beats and pending_task is None:
                    pending_task = task
                    pending_schedule_idx = idx

            elif schedule.mode == ScheduleMode.SCHEDULED:
                if self._is_scheduled_now(schedule.schedule_times, schedule.last_run_date) and pending_task is None:
                    pending_task = task
                    pending_schedule_idx = idx

        # 空闲评估：本 tick 无其他到期任务时，idle 调度按"连续空闲拍数/待反思
        # 标记"触发（确定性调度优先，空闲窗口让位给它们）
        if pending_task is None:
            idle_hit = self._evaluate_idle_schedule()
            if idle_hit is not None:
                pending_schedule_idx, pending_task, pending_extra_note = idle_hit
                dirty = True
            elif any(s.mode == ScheduleMode.IDLE for s in self.config.task_schedules):
                dirty = True  # idle 空闲计数已更新（清零或递增），需落盘

        if pending_task is not None and pending_schedule_idx >= 0:
            schedule = self.config.task_schedules[pending_schedule_idx]

            entity = await self._pop_analysis_entity() if pending_task.scope.value == "entity" else None
            self._task_inflight.add(pending_task.name)
            try:
                await self.executor.run(
                    pending_task, entity,
                    temperature=self.config.analysis_temperature,
                    model_id=schedule.model_id,
                    reasoning_effort=schedule.reasoning_effort,
                    extra_note=pending_extra_note,
                )
            except Exception as exc:
                # at-least-once：执行失败不更新标记，下个 tick 重试；
                # 连续失败超过上限则放弃本轮（避免故障任务卡死调度）
                failures = self._task_failures.get(pending_task.name, 0) + 1
                self._task_failures[pending_task.name] = failures
                if failures < self._MAX_TASK_FAILURES:
                    log(f"任务 [{pending_task.name}] 执行失败（第 {failures} 次），"
                        f"保留标记待重试: {exc}", "WARNING", tag="心跳")
                    if dirty:
                        self.config.save()
                    return executed
                log(f"任务 [{pending_task.name}] 连续 {failures} 次失败，放弃本轮: {exc}",
                    "ERROR", tag="心跳")
            else:
                self._task_failures.pop(pending_task.name, None)
                executed.append(pending_task.name)
            finally:
                self._task_inflight.discard(pending_task.name)

            # at-least-once：先执行任务，成功后才更新标记并原子落盘；
            # 若执行中途崩溃，标记未更新，下次 tick 会重新触发
            if schedule.mode == ScheduleMode.HEARTBEAT:
                schedule.beat_count = 0
            elif schedule.mode == ScheduleMode.SCHEDULED:
                schedule.last_run_date = datetime.now().strftime("%Y-%m-%d")
            elif schedule.mode == ScheduleMode.IDLE:
                # 任务执行经 mind.reflect 已刷新思考活动 → 下次评估计数自然归零，
                # 此处仅复位持久化计数并消费待反思标记
                schedule.beat_count = 0
                self._reflection_pending = ""
            dirty = True

        if dirty:
            self.config.save()
        return executed

    def _evaluate_idle_schedule(self) -> Optional[tuple[int, TaskDefinition, str]]:
        """评估 idle 调度（全局仅一条）：更新空闲计数并判定是否触发。

        计数规则：自上次 tick 以来有真实思考活动（mind.last_activity_ts 前移，
        覆盖回复/反思/任务执行，含 idle 任务自身）→ 清零；否则 +1。
        返回 (schedule_idx, task, extra_note)；未配置或未达阈值/任务不可用返回 None。
        """
        for idx, schedule in enumerate(self.config.task_schedules):
            if schedule.mode != ScheduleMode.IDLE:
                continue
            task = self.task_registry.get(schedule.task_name)
            if not task or not task.enabled or schedule.task_name in self._task_inflight:
                return None
            activity_ts = float(getattr(self.mind, "last_activity_ts", 0.0) or 0.0)
            if activity_ts > self._idle_anchor_ts:
                self._idle_anchor_ts = activity_ts
                schedule.beat_count = 0
            else:
                schedule.beat_count += 1
            if schedule.beat_count >= schedule.every_n_beats or self._reflection_pending:
                extra_note = ""
                if self._reflection_pending:
                    extra_note = f"\n\n[反思触发原因]\n{self._reflection_pending}"
                return idx, task, extra_note
            return None
        return None

    # ------------------------------------------------------------------
    # 手动触发任务
    # ------------------------------------------------------------------

    def start_task_background(self, task_name: str) -> tuple[bool, str]:
        """校验并后台启动任务（供 AI 工具调用，不阻塞当前思维循环）。

        受理后任务在独立 asyncio Task 中经 run_task 执行（_task_inflight 去重
        与 _tick_lock 互斥由 run_task 保证，此处的 inflight 检查仅为提前给出
        准确反馈）；任务执行中再次触发同名任务会被 run_task 忽略。

        Returns:
            (是否受理, 说明文案)
        """
        task = self.task_registry.get(task_name)
        if not task:
            return False, f"任务 {task_name} 不存在"
        if not task.enabled:
            return False, f"任务 {task_name} 已禁用"
        if task_name in self._task_inflight:
            return False, f"任务 {task_name} 已在执行或排队中"
        asyncio.create_task(
            self._run_task_guarded(task_name),
            name=f"agent.heartbeat.manual.{task_name}",
        )
        return True, "任务已受理，正在后台执行"

    async def _run_task_guarded(self, task_name: str) -> None:
        """后台任务执行壳：吞掉异常只记日志（run_task 内部已分级处理）。"""
        try:
            result = await self.run_task(task_name)
            log(f"后台手动任务完成: {task_name} ({'有产出' if result else '无产出'})", tag="心跳")
        except Exception as exc:
            log(f"后台手动任务异常 [{task_name}]: {exc}", "WARNING", tag="心跳")

    async def run_task(self, task_name: str) -> Optional[str]:
        """按名称执行指定任务，返回产出内容或 None。

        与 tick 共用同一把锁，避免手动触发与调度心跳并发执行同一任务；
        同一任务已在执行/排队中时直接忽略重复触发（排队里同一种任务只允许一条）。
        """
        task = self.task_registry.get(task_name)
        if not task:
            log(f"任务 [{task_name}] 不存在", "WARNING", tag="心跳")
            return None
        if not task.enabled:
            log(f"任务 [{task_name}] 已禁用", "WARNING", tag="心跳")
            return None
        # check-then-set 间无 await，asyncio 单线程下无竞态
        if task_name in self._task_inflight:
            log(f"任务 [{task_name}] 已在执行或排队中，忽略重复触发", "WARNING", tag="心跳")
            return None
        self._task_inflight.add(task_name)

        log(f"手动执行任务: {task_name}", tag="心跳")

        # 会话生命周期由 thinking_session 管理：退出（含异常）保证发射 SESSION_END，
        # 且与并发的思维会话按执行上下文隔离，互不干扰
        try:
            async with self._tick_lock:
                async with thinking_session({
                    "is_heartbeat": True, "is_introspection": True, "entity": "任务执行",
                }) as session:
                    session.end["reason"] = "task_completed"
                    entity = await self._pop_analysis_entity() if task.scope.value == "entity" else None
                    try:
                        result = await self.executor.run(
                            task, entity, temperature=self.config.analysis_temperature,
                        )
                    except Exception as exc:
                        log(f"手动任务 [{task_name}] 执行失败: {exc}", "WARNING", tag="心跳")
                        return None
                    return result.content if result else None
        finally:
            self._task_inflight.discard(task_name)

    # ------------------------------------------------------------------
    # 反思延迟消费（元决策 REFLECT → 空闲窗口）
    # ------------------------------------------------------------------

    def mark_reflection_pending(self, reason: str) -> None:
        """登记待执行反思：idle 调度在下个空闲心跳消费，不打断对话节奏。"""
        self._reflection_pending = (reason or "").strip()

    @property
    def reflection_pending(self) -> bool:
        """是否存在待执行反思（消费于 idle 任务触发时）。"""
        return bool(self._reflection_pending)

    def has_idle_schedule(self) -> bool:
        """是否配置了 idle 模式调度（决定 REFLECT 决策延迟还是立即执行）。"""
        return any(s.mode == ScheduleMode.IDLE for s in self.config.task_schedules)

    # ------------------------------------------------------------------
    # 内置维护
    # ------------------------------------------------------------------

    async def _maintain_conversation_folds(self) -> None:
        """空闲自动折叠：连续 N 个心跳无外部新消息的会话，把窗口积压折进摘要。

        把折叠（摘要块重写 = 缓存前缀断点）从活跃对话期转移到无人时段：
        空闲判定按心跳计数（conversation_fold_idle_beats）且只认外部消息
        （list_scope_activity 只统计 role=user，任务/系统写入不算）；
        积压阈值派生自窗口参数（ConversationData.fold_idle_min = M−x）。
        折叠完成由 folder 钩子自动预热缓存。
        """
        try:
            from agent.storage.conversation_fold import (
                fold_idle_beats,
                is_summary_enabled,
            )
            if not is_summary_enabled():
                return
            conv_data = self.mind.conversation_data
            activity = await conv_data.list_scope_activity()
            seen: Dict[str, int] = self._fold_activity_ts
            for scope_type, scope_id, max_ts in activity:
                key = f"{scope_type}:{scope_id}"
                beats = self._fold_idle_beats
                if max_ts > seen.get(key, 0):
                    seen[key] = max_ts
                    beats[key] = 0
                    continue
                beats[key] = beats.get(key, 0) + 1
                if beats[key] < fold_idle_beats():
                    continue
                backlog = await conv_data.scope_backlog(scope_type, scope_id)
                if backlog < conv_data.fold_idle_min:
                    continue
                if await conv_data.schedule_fold(scope_type, scope_id):
                    log(
                        f"空闲自动折叠: {key}（{beats[key]} 心跳无新消息，积压 {backlog} 条）",
                        "DEBUG", tag="心跳",
                    )
        except Exception as e:
            log(f"空闲折叠扫描失败: {e}", "DEBUG", tag="心跳")

    async def _run_maintenance(self) -> None:
        """内置维护步骤（不可由用户配置为任务）。"""
        try:
            from agent.memory.notes import consolidate_heartbeat
            consolidate_heartbeat()
        except Exception as e:
            log(f"心跳日志合并失败: {e}", "DEBUG", tag="心跳")

        await self._maintain_conversation_folds()

        try:
            saved = await self.mind.everything_data.save_all_entity_counters()
            if saved:
                log(f"心跳持久化实体计数: {saved} 个", "DEBUG", tag="心跳")
        except Exception as e:
            log(f"实体计数持久化失败: {e}", "DEBUG", tag="心跳")

        # 类型计数在同 tick 内被健康检查与状态区块复用，只查一次；
        # 整理 tick 计数可能变化，状态区块到时重查（见 _write_memory_status）
        type_counts: Optional[Dict[str, int]] = None
        if self.mind.memory_store:
            try:
                type_counts = await self.mind.memory_store.get_type_counts()
            except Exception:
                type_counts = None
        memory_warnings = await self._check_memory_health(type_counts)
        for warn in memory_warnings:
            hb_log.append_entry(f"[记忆预警] {warn}")

        # 记忆整理：遗忘低价值记忆 + 类型上限 + 高相似合并 + cognee 同步（人脑"睡眠整理"）
        # 全量扫描成本较高，按每 N 个 tick 执行一次（默认 12，约每小时一次）
        consolidate_report = None
        try:
            from core.config import get_config_int
            consolidate_every = max(1, get_config_int("memory_consolidate_every_n_ticks", 12))
            if self.mind.memory_store and self._total_ticks % consolidate_every == 0:
                from agent.memory.consolidator import MemoryConsolidator
                consolidate_report = await MemoryConsolidator(self.mind.memory_store).consolidate()
                for line in consolidate_report.to_log_lines():
                    hb_log.append_entry(f"[记忆整理] {line}")
        except Exception as e:
            log(f"记忆整理失败: {e}", "DEBUG", tag="心跳")

        # 自动记忆捕获：对话达轮数阈值或静默空闲时提取事实进长期记忆
        try:
            from agent.memory.auto_capture import run_auto_capture
            await run_auto_capture(self.mind)
        except Exception as e:
            log(f"自动记忆捕获失败: {e}", "DEBUG", tag="心跳")

        # 主便签记忆状态区块：让 AI 随时了解自己的记忆情况（内容不变时不写）
        await self._write_memory_status(consolidate_report, type_counts)

        # 过期日期便签归档：提炼进长期记忆（自动同步 cognee）后删除文件
        try:
            await self._archive_expired_events()
        except Exception as e:
            log(f"日期便签归档失败: {e}", "DEBUG", tag="心跳")

        # 技能策展：重力迁移（长期未真实使用降级/归档）+ 向量预热 + 治理议程
        try:
            curator = getattr(self.mind, "skill_curator", None)
            if curator is not None:
                report = curator.apply_automatic_transitions()
                if report["staled"] or report["archived"]:
                    hb_log.append_entry(
                        f"[技能策展] 降级 {len(report['staled'])} 个，"
                        f"归档 {len(report['archived'])} 个"
                    )
                await curator.warm_index()
                agenda = await curator.build_agenda()
                summary = curator.agenda_summary(agenda)
                if summary:
                    hb_log.append_entry(f"[技能治理议程] {summary}")
        except Exception as e:
            log(f"技能策展失败: {e}", "DEBUG", tag="心跳")

        entity = await self._pop_analysis_entity()
        if entity:
            try:
                result = await self._run_entity_analysis(entity)
            except Exception as exc:
                result = None
                log(f"实体画像分析异常: {entity.get_entity_desc()} -> {exc}", "WARNING", tag="心跳")
            if result is None:
                # 分析未执行（对话不足/失败）：有限次重入队，超过上限放弃并显式记录
                key = (entity.group_id or 0, entity.uid or 0, entity.adapter_key or "")
                attempts = self._analysis_attempts.get(key, 0) + 1
                if attempts < self._MAX_ANALYSIS_ATTEMPTS:
                    self._analysis_attempts[key] = attempts
                    self.mind.pfc.requeue_analysis(key[0], key[1], key[2])
                    log(f"实体画像分析推迟重试（第 {attempts} 次）: {entity.get_entity_desc()}",
                        "DEBUG", tag="心跳")
                else:
                    self._analysis_attempts.pop(key, None)
                    log(f"实体画像分析连续 {attempts} 次未执行，放弃: {entity.get_entity_desc()}",
                        "WARNING", tag="心跳")
            else:
                self._analysis_attempts.pop(
                    (entity.group_id or 0, entity.uid or 0, entity.adapter_key or ""), None
                )

        # 上下文提供者 on_tick 钩子（实体自驱维护周期）
        try:
            from core.context_provider import ContextProviderRegistry
            await ContextProviderRegistry.tick_all()
        except Exception as e:
            log(f"上下文提供者 tick 失败: {e}", "DEBUG", tag="心跳")

        # 实体 Lifecycle on_tick 钩子（如 share_store.sweep_expired）
        try:
            from core.lifecycle import Lifecycle
            await Lifecycle.tick_all()
        except Exception as e:
            log(f"Lifecycle tick 失败: {e}", "DEBUG", tag="心跳")

    async def _check_memory_health(
        self, type_counts: Optional[Dict[str, int]] = None,
    ) -> List[str]:
        """记忆健康检查：纯逻辑检查阈值（与 memory_stats 共用同一配置口径）。"""
        if not self.mind.memory_store:
            return []
        from core.config import get_config_int
        warn_threshold = get_config_int("memory_warn_threshold", 200)
        warnings: List[str] = []
        try:
            if type_counts is None:
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
                if count > warn_threshold:
                    warnings.append(f"{mem_type} 记忆有 {count} 条（阈值 {warn_threshold}），建议整理")
        except Exception as exc:
            log(f"记忆阈值检查异常: {exc}", "WARNING", tag="心跳")
        if warnings:
            log(f"记忆阈值预警: {len(warnings)} 条", tag="心跳")
        return warnings

    # 便签容量建议（行数），与主便签指南中的容量建议保持一致
    _NOTES_CAPACITY = {
        "knowledge.md": 500,
        "reflections.md": 500,
        "entities.md": 1000,
    }

    async def _write_memory_status(
        self, consolidate_report: Any = None,
        type_counts: Optional[Dict[str, int]] = None,
    ) -> None:
        """将记忆系统状态写入主便签受管区块（让 AI 随时了解自己的记忆情况）。"""
        store = self.mind.memory_store
        if not store:
            return
        try:
            from agent.memory import notes as notes_mod
            # 整理 tick 计数可能已变化，需重查；其余 tick 复用健康检查的结果
            if type_counts is None or consolidate_report is not None:
                type_counts = await store.get_type_counts()
            total = sum(type_counts.values())
            archived = await store.count_archived()
            dist = " / ".join(
                f"{t} {c}" for t, c in sorted(type_counts.items(), key=lambda kv: -kv[1])
            )
            lines = [
                "## 记忆系统状态（系统自动维护，勿手改）",
                "",
                f"- 活跃记忆：{total} 条（{dist}）",
                f"- 已归档：{archived} 条（遗忘/手动归档，可由系统恢复）",
            ]
            try:
                from agent.memory.cognee.config import load_cognee_config
                if load_cognee_config().enabled:
                    from agent.memory.cognee.runtime import get_cognee_coordinator
                    coordinator = get_cognee_coordinator()
                    pending = 0
                    paused_note = ""
                    if coordinator is not None:
                        status = await coordinator.status()
                        pending = getattr(status, "pending", 0) or 0
                        if getattr(status, "paused", False):
                            until = time.strftime(
                                "%H:%M", time.localtime(status.paused_until or 0),
                            )
                            paused_note = f"（写盘熔断暂停中，至 {until} 自动恢复）"
                    lines.append(
                        f"- cognee 图谱：已启用，同步积压 {pending} 条{paused_note}"
                    )
                else:
                    lines.append("- cognee 图谱：未启用")
            except Exception:
                pass
            if consolidate_report is not None:
                parts: List[str] = []
                if consolidate_report.forgotten_count:
                    parts.append(f"遗忘 {consolidate_report.forgotten_count}")
                if consolidate_report.relaxed_count:
                    parts.append(f"松弛 {consolidate_report.relaxed_count}")
                if consolidate_report.merged_count:
                    parts.append(f"合并 {consolidate_report.merged_count}")
                stamp = datetime.now().strftime("%m-%d %H:%M")
                lines.append(f"- 最近整理（{stamp}）：{' · '.join(parts) if parts else '无需整理'}")
            # 运行指标（召回通道/写入去重/自动捕获累计计数）
            try:
                from agent.memory import metrics as memory_metrics
                lines.extend(memory_metrics.render_status_lines())
            except Exception:
                pass
            # 近 24h 记忆变更摘要（审计事件流消费口）
            try:
                audit = await store.get_audit_summary(hours=24)
                if audit:
                    action_names = {"update": "更新", "delete": "删除",
                                    "archive": "归档", "merge": "合并"}
                    detail = " · ".join(
                        f"{action_names.get(a, a)} {n}"
                        for a, n in sorted(audit.items(), key=lambda kv: -kv[1])
                    )
                    lines.append(f"- 近 24h 记忆变更：{detail}")
            except Exception:
                pass
            for fname, cap in self._NOTES_CAPACITY.items():
                fpath = notes_mod.get_memory_dir() / fname
                if fpath.exists():
                    with fpath.open(encoding="utf-8") as fp:
                        line_count = sum(1 for _ in fp)
                    if line_count > cap:
                        lines.append(f"- ⚠️ 便签超标：{fname} {line_count} 行（建议 ≤{cap}），需提炼压缩")
            await notes_mod.update_memory_status_block_async("\n".join(lines))
        except Exception as exc:
            log(f"记忆状态区块写入失败: {exc}", "DEBUG", tag="心跳")

    # ------------------------------------------------------------------
    # 过期日期便签归档
    # ------------------------------------------------------------------

    async def _archive_expired_events(self) -> None:
        """归档超过保留期的 events 日期便签。

        每个过期文件：先经 LLM 提炼长期价值要点写入数据库长期记忆
        （store.add 自动触发 cognee 投影），再删除文件并重建 chunks 索引。
        提炼失败时保留文件，下次心跳重试。
        """
        from agent.memory import notes as notes_mod
        from core.config import get_config_bool, get_config_int

        retention = get_config_int("notes_events_retention_days", 30)
        expired = notes_mod.list_expired_events(retention)
        if not expired:
            return

        distill = get_config_bool("notes_events_distill_enabled", True)
        deleted = False
        for note in expired[:_ARCHIVE_MAX_PER_TICK]:
            try:
                content = notes_mod.read_memory_file(note.path)
                if distill and content.strip():
                    distilled = await self._distill_event_note(note.date, content)
                    if distilled is None:
                        continue
                    if distilled:
                        await self._store_distilled_event(note.date, distilled)
                        hb_log.append_entry(
                            f"[事件归档] {note.date}: 提炼 {len(distilled)} 字要点，文件已删除"
                        )
                    else:
                        hb_log.append_entry(f"[事件归档] {note.date}: 无长期价值，文件已删除")
                else:
                    hb_log.append_entry(f"[事件归档] {note.date}: 文件已删除")
                notes_mod.delete_memory_file(note.path)
                deleted = True
                log(f"过期日期便签已归档: {note.path}", tag="心跳")
            except Exception as exc:
                log(f"日期便签归档失败 {note.path}: {exc}", "WARNING", tag="心跳")

        if deleted:
            await self._resync_file_index()

    async def _distill_event_note(self, note_date: str, content: str) -> Optional[str]:
        """提炼日期便签的长期价值要点。

        返回要点文本；无保留价值返回空字符串；提炼失败返回 None（调用方保留文件重试）。
        """
        prompt = _EVENT_DISTILL_PROMPT.replace("{date}", note_date).replace(
            "{content}", content[:_DISTILL_MAX_CHARS]
        )
        try:
            raw = await self.mind.reflect(
                [{"role": "user", "content": f"[系统任务 - events_archive]\n{prompt}"}],
                options={"temperature": 0.3},
            )
        except Exception as exc:
            log(f"日期便签提炼失败 {note_date}: {exc}", "WARNING", tag="心跳")
            return None
        text = _clean_llm(raw)
        if not text or text.startswith("无"):
            return ""
        return text

    async def _store_distilled_event(self, note_date: str, distilled: str) -> None:
        """将提炼要点写入数据库长期记忆（自动进入 cognee 投影队列）。

        经 LLM 去重裁决：auto_capture 可能已从原始对话提取过相同事实，
        直接写入会产生重复（语义重复 skip / 事实演进 update / 无重复 store）。
        """
        store = self.mind.memory_store
        if not store:
            return
        content = f"[{note_date} 事件归档]\n{distilled}"
        tags = ["type:event", f"date:{note_date}"]
        try:
            from agent.memory import metrics
            from agent.memory.dedup import (
                apply_update,
                gather_dedup_candidates,
                judge_write,
            )
            candidates = await gather_dedup_candidates(store, self.mind.embedder, content)
            decision = await judge_write(content, candidates)
            action = decision.get("action", "store")
            metrics.incr(f"write.dedup_llm_{action}")
            if action == "skip":
                log(f"事件归档去重: {note_date} 已有等价记忆，跳过", "DEBUG", tag="心跳")
                return
            if action == "update" and decision.get("target_id"):
                updated = await apply_update(
                    store, int(decision["target_id"]),
                    str(decision.get("content") or content), tags,
                )
                if updated is not None:
                    return
            if action == "merge" and decision.get("target_ids"):
                new_id = await store.merge_memories(
                    [int(i) for i in decision["target_ids"]],
                    str(decision.get("content") or content),
                )
                if new_id:
                    return
        except Exception as exc:
            log(f"事件归档去重裁决失败，直接写入: {exc}", "DEBUG", tag="心跳")
        entry = MemoryEntry(
            memory_type=MemoryType.EPISODIC,
            content=content,
            source="events_archive",
            tags=tags,
            importance=0.6,
        )
        await store.add(entry)
        from agent.memory.embedding import wake_embedding_worker
        wake_embedding_worker()

    async def _resync_file_index(self) -> None:
        """归档删除文件后重建 chunks 索引（清理已删文件的索引项）。"""
        store = self.mind.memory_store
        if not store:
            return
        try:
            from agent.memory.memory_sync import sync_files
            from agent.memory.notes import get_workspace_dir
            await sync_files(store, self.mind.embedder, get_workspace_dir())
        except Exception as exc:
            log(f"归档后索引同步失败: {exc}", "DEBUG", tag="心跳")

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

        # 仅用户实体需要额外拉取其私聊对话；群组实体无 uid，避免对不存在的 user 0 查询
        combined = conversation
        if entity.uid:
            user_query_entity = MessageAssistant(uid=entity.uid, adapter_key=entity.adapter_key)
            user_conv = await self.mind.get_conversation(user_query_entity)
            combined = conversation + user_conv

        alias_convs = await self._collect_alias_conversations(entity)
        if alias_convs:
            combined = combined + alias_convs

        from core.config import get_config_bool
        base_messages = await self.mind.get_recollection(
            combined, lean=get_config_bool("task_lean_context", True),
        )
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

        # 覆盖式更新前备份旧画像（防坏写不可恢复）
        try:
            scope_type0, scope_id0 = entity.identity_parts
            sqlite0 = self.mind.everything_data.router.sqlite
            old_profile = await sqlite0.get_entity_personality(
                scope_type=scope_type0, scope_id=scope_id0,
            )
            if old_profile and old_profile.get("personality"):
                from agent.memory.profile_backup import backup_entity_profile
                backup_entity_profile(scope_type0, scope_id0, old_profile["personality"])
        except Exception as exc:
            log(f"画像备份失败: {exc}", "DEBUG", tag="心跳")

        entity.set_personality(content)
        await self.mind.everything_data.save_entity_personality(entity)
        log(f"实体画像更新: {desc} -> {content[:80]}", tag="心跳")

        # 画像记忆以含 adapter 的身份 scope_id 为标识，跨频道同号实体互不串档
        identity_type, identity_id = entity.identity_parts
        source = f"entity_{identity_id}"
        scope_tag = f"{identity_type}:{identity_id}"

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
            await self.mind.memory_store.add(entry)
            from agent.memory.embedding import wake_embedding_worker
            wake_embedding_worker()

        # 关系抽取：复用同一份对话材料提炼结构化关系事实（失败不影响画像产出）
        if self.mind.memory_store:
            try:
                from agent.memory.graph.extract import extract_and_store_relations
                added = await extract_and_store_relations(self.mind, entity, combined)
                if added:
                    hb_log.append_entry(f"[relation_extract] {desc}: +{added} 条关系")
            except Exception as exc:
                log(f"关系抽取失败: {desc} -> {exc}", "DEBUG", tag="心跳")

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
        except Exception as exc:
            log(f"弹出画像分析实体失败: {exc}", "WARNING", tag="心跳")
            return None

    async def _collect_alias_conversations(self, entity: "EntityData") -> List[Dict[str, Any]]:
        """收集所有 alias 关联身份的对话记录。"""
        try:
            from agent.messages import MessageAssistant, MessageAssistantGroup
            sqlite = self.mind.everything_data.router.sqlite
            scope_type, scope_id = entity.identity_parts
            primary = await sqlite.resolve_alias(scope_type, scope_id)
            p_type, p_id = primary if primary else (scope_type, scope_id)
            aliases = await sqlite.get_aliases_for_primary(p_type, p_id)
            all_ids = [(p_type, p_id)] + [(a["scope_type"], a["scope_id"]) for a in aliases]
            current = (scope_type, scope_id)
            extra: List[Dict[str, Any]] = []
            for id_type, id_id in all_ids:
                if (id_type, id_id) == current:
                    continue
                # id_id 已是含 adapter 前缀的 scope_id；以裸 adapter 构造使 scope_id 原样命中
                # 群组身份用 MessageAssistantGroup（携带 group_id 字段），用户用 MessageAssistant
                alias_entity = (
                    MessageAssistantGroup(group_id=id_id)
                    if id_type == "group"
                    else MessageAssistant(uid=id_id)
                )
                conv = await self.mind.get_conversation(alias_entity)
                if conv:
                    extra.extend(conv)
            return extra
        except Exception as exc:
            log(f"alias 对话收集失败: {exc}", "WARNING", tag="心跳")
            return []

    def _is_scheduled_now(self, times: List[str], last_run_date: str) -> bool:
        """检查当前时间是否匹配调度时间（且今天未执行过）。

        支持跨午夜补触发：调度时间在昨日深夜且距今不超过一个 tick 间隔时
        视为到期，避免 tick 恰好跨过（如 23:50 → 次日 00:05）导致当天任务错过。
        """
        now = datetime.now()
        today = now.strftime("%Y-%m-%d")
        if last_run_date == today:
            return False
        current_minutes = now.hour * 60 + now.minute
        interval_minutes = max(1, int(getattr(self.config, "interval_seconds", 60)) // 60)
        for t in times:
            try:
                hh, mm = str(t).strip().split(":", 1)
                target = int(hh) * 60 + int(mm)
                if not (0 <= target < 1440):
                    continue
            except (ValueError, TypeError):
                continue
            if current_minutes >= target:
                return True
            # 跨午夜窗口：目标在昨日深夜，距今不超过一个 tick 间隔
            if (1440 - target) + current_minutes <= interval_minutes:
                return True
        return False

    def get_status(self) -> Dict[str, Any]:
        """返回心跳引擎运行状态。"""
        activity_ts = float(getattr(self.mind, "last_activity_ts", 0.0) or 0.0)
        return {
            "enabled": self.config.enabled,
            "interval_seconds": self.config.interval_seconds,
            "total_ticks": self._total_ticks,
            "task_count": len(self.task_registry.list_all()),
            "schedule_count": len(self.config.task_schedules),
            "last_activity_sec": int(time.time() - activity_ts) if activity_ts > 0 else None,
            "reflection_pending": self.reflection_pending,
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
