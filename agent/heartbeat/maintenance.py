"""心跳维护流水线：在已完成阶段的安全边界交回主调度器。

模型看到：原有维护、捕获与画像结果，待办消息可在阶段之间先得到处理。
Token：不增加模型调用，不重跑已完成阶段。
缓存：不改变稳定提示词；调度回调不在数据库事务或捕获锁内执行。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Dict, Optional

from core.log import log

from . import log as hb_log

if TYPE_CHECKING:
    from .engine import HeartbeatEngine


async def run_maintenance(
    engine: HeartbeatEngine, *, checkpoint: Callable[[], Awaitable[None]] | None = None,
    should_yield: Callable[[], bool] | None = None,
) -> None:
    """依次维护，各阶段独立容错，取消持续向上传播。"""
    from core.config import get_config_bool

    async def boundary() -> None:
        if checkpoint is not None:
            await checkpoint()

    try:
        if get_config_bool("auto_consolidate_enabled", True):
            from agent.memory.notes import consolidate_heartbeat

            consolidate_heartbeat()
    except Exception as e:
        log(f"心跳日志合并失败: {e}", "DEBUG", tag="心跳")

    await boundary()

    await engine._maintain_conversation_folds()

    try:
        saved = await engine.everything_data.save_all_entity_counters()
        if saved:
            log(f"心跳持久化实体计数: {saved} 个", "DEBUG", tag="心跳")
    except Exception as e:
        log(f"实体计数持久化失败: {e}", "DEBUG", tag="心跳")

    await boundary()

    # 类型计数在同 tick 内被健康检查与状态区块复用，只查一次；
    # 整理 tick 计数可能变化，状态区块到时重查（见 _write_memory_status）
    type_counts: Optional[Dict[str, int]] = None
    if engine.memory.memory_store:
        try:
            type_counts = await engine.memory.memory_store.get_type_counts()
        except Exception:
            type_counts = None
    memory_warnings = await engine._check_memory_health(type_counts)
    for warn in memory_warnings:
        hb_log.append_entry(f"[记忆预警] {warn}")

    await boundary()

    # 记忆整理：遗忘低价值记忆 + 类型上限 + 高相似合并 + cognee 同步（人脑"睡眠整理"）
    # 全量扫描成本较高，按每 N 个 tick 执行一次（默认 12，约每小时一次）
    consolidate_report = None
    try:
        from core.config import get_config_int

        consolidate_every = max(1, get_config_int("memory_consolidate_every_n_ticks", 12))
        if engine.memory.memory_store and engine._total_ticks % consolidate_every == 0:
            from agent.memory.consolidator import MemoryConsolidator

            consolidate_report = await MemoryConsolidator(engine.memory.memory_store).consolidate()
            for line in consolidate_report.to_log_lines():
                hb_log.append_entry(f"[记忆整理] {line}")
    except Exception as e:
        log(f"记忆整理失败: {e}", "DEBUG", tag="心跳")

    await boundary()

    # 记忆文件索引周期同步：增量比对 hash，未变文件零写入；
    # 让 uploads/docs 的外部新增/修改在进程运行期内即可被检索到
    try:
        from core.config import get_config_int

        sync_every = max(1, get_config_int("memory_file_sync_every_n_ticks", 12))
        if engine.memory.memory_store and engine._total_ticks % sync_every == 0:
            await engine._resync_file_index()
    except Exception as e:
        log(f"文件索引周期同步失败: {e}", "DEBUG", tag="心跳")

    await boundary()

    # 自动记忆捕获：对话达轮数阈值或静默空闲时提取事实进长期记忆
    try:
        from agent.memory.auto_capture import run_auto_capture

        await run_auto_capture(engine.memory, checkpoint=checkpoint, should_yield=should_yield)
    except Exception as e:
        log(f"自动记忆捕获失败: {e}", "DEBUG", tag="心跳")

    await boundary()

    # 目标停滞概况：长期未更新的目标只呈现事实（心跳日志一行），
    # 存续判断权在 AI——长期目标是规划常态，系统不做基于时间的自动清理
    try:
        from agent.planning.situation import stale_goal_line

        stale_line = await stale_goal_line()
        if stale_line:
            hb_log.append_entry(stale_line)
    except Exception as e:
        log(f"目标停滞概况构建失败: {e}", "DEBUG", tag="心跳")

    await boundary()

    # 主标签记忆（main:hub）自愈：缺失/被清理时重建骨架（幂等）
    if engine.memory.memory_store:
        try:
            from agent.memory.hub import ensure_hub

            if await ensure_hub(engine.memory.memory_store):
                hb_log.append_entry("[主标签记忆] 骨架缺失，已重建")
        except Exception as e:
            log(f"主标签记忆自愈失败: {e}", "DEBUG", tag="心跳")

    await boundary()

    # 主便签记忆状态区块：让 AI 随时了解自己的记忆情况（内容不变时不写）
    await engine._write_memory_status(consolidate_report, None if checkpoint is not None else type_counts)

    # 主便签心跳态势区块：让 AI 在任意会话了解自身调度与任务运行情况
    # （内容不变时不写；不含逐拍计数，保证任务未执行期间字节稳定）
    await engine._write_heartbeat_status()

    await boundary()

    # 过期日期便签归档：提炼进长期记忆（自动同步 cognee）后删除文件
    try:
        await engine._archive_expired_events()
    except Exception as e:
        log(f"日期便签归档失败: {e}", "DEBUG", tag="心跳")

    await boundary()

    # 技能重力迁移与治理议程按维护周期执行，向量回填由 EmbeddingWorker 负责。
    try:
        skills = engine.skills
        curator = skills.curator if skills is not None else None
        if curator is not None:
            from core.config import get_config_int

            curator_every = max(1, get_config_int("skills_curator_every_n_ticks", 12))
            if engine._total_ticks % curator_every == 0:
                report = curator.apply_automatic_transitions()
                if report["staled"] or report["archived"]:
                    hb_log.append_entry(
                        f"[技能策展] 降级 {len(report['staled'])} 个，归档 {len(report['archived'])} 个"
                    )
                agenda = await curator.build_agenda()
                summary = curator.agenda_summary(agenda)
                if summary:
                    hb_log.append_entry(f"[技能治理议程] {summary}")
    except Exception as e:
        log(f"技能策展失败: {e}", "DEBUG", tag="心跳")

    # 图谱治理议程：确定性事实摘要（弱边/陈旧/歧义/重复/枢纽），
    # AI 经 graph_curation 任务消费完整议程并用图谱工具执行治理
    try:
        if engine.memory.memory_store is not None:
            from agent.memory.graph.curation import agenda_summary, build_agenda

            graph_agenda = await build_agenda(engine.memory.memory_store.graph)
            graph_summary = agenda_summary(graph_agenda)
            if graph_summary:
                hb_log.append_entry(f"[图谱治理议程] {graph_summary}")
    except Exception as e:
        log(f"图谱治理议程构建失败: {e}", "DEBUG", tag="心跳")

    await boundary()

    # 空会话清理：从未收到过用户消息的会话（历史缺陷与一次性通知残留）
    await engine._sweep_empty_conversations_if_due()

    await boundary()

    entity = await engine._pop_analysis_entity()
    if entity:
        try:
            result = await engine._run_entity_analysis(entity)
        except Exception as exc:
            result = None
            log(f"实体画像分析异常: {entity.get_entity_desc()} -> {exc}", "WARNING", tag="心跳")
        if result is None:
            # 分析未执行（对话不足/失败）：有限次重入队，超过上限放弃并显式记录
            key = (entity.group_id or 0, entity.uid or 0, entity.adapter_key or "")
            attempts = engine._analysis_attempts.get(key, 0) + 1
            if attempts < engine._MAX_ANALYSIS_ATTEMPTS:
                engine._analysis_attempts[key] = attempts
                engine.mind.pfc.requeue_analysis(key[0], key[1], key[2])
                log(f"实体画像分析推迟重试（第 {attempts} 次）: {entity.get_entity_desc()}", "DEBUG", tag="心跳")
            else:
                engine._analysis_attempts.pop(key, None)
                log(
                    f"实体画像分析连续 {attempts} 次未执行，放弃: {entity.get_entity_desc()}", "WARNING", tag="心跳"
                )
        else:
            engine._analysis_attempts.pop((entity.group_id or 0, entity.uid or 0, entity.adapter_key or ""), None)

    await boundary()

    # 上下文提供者 on_tick 钩子（实体自驱维护周期）
    try:
        from core.context_provider import ContextProviderRegistry

        await ContextProviderRegistry.tick_all()
    except Exception as e:
        log(f"上下文提供者 tick 失败: {e}", "DEBUG", tag="心跳")

    await boundary()

    # 实体 Lifecycle on_tick 钩子（如 share_store.sweep_expired）
    try:
        from core.lifecycle import Lifecycle

        await Lifecycle.tick_all()
    except Exception as e:
        log(f"Lifecycle tick 失败: {e}", "DEBUG", tag="心跳")
