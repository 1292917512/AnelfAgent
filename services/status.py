"""智能体状态服务 -- 状态轮询、组件信息、Mind 配置。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from core.log import log
from services._runtime import get_agent_app, get_runtime, is_ready


class AgentStatusService:

    def is_ready(self) -> bool:
        return is_ready()

    def get_status(self) -> Optional[Dict[str, Any]]:
        """返回智能体运行时状态摘要，未就绪返回 None。"""
        app = get_agent_app()
        if app is None:
            return None
        return app.get_status_info()

    def get_component_info(self) -> List[str]:
        """收集运行时组件概要信息，返回可展示的文本行列表。"""
        lines: List[str] = []
        rt = get_runtime()
        if rt is None:
            return ["运行时尚未初始化"]
        try:
            lines.append(f"LLM:          {rt.llm.__class__.__name__}")
            if hasattr(rt.llm, "model"):
                lines.append(f"  模型:       {rt.llm.model}")
            lines.append(f"存储:         {rt.data_center.__class__.__name__}")
            lines.append(f"  SQLite:     {rt.data_center.sqlite.__class__.__name__}")

            from core.entity import EntityRegistry, EntityType
            tool_entities = EntityRegistry.get_by_type(EntityType.TOOL)
            if tool_entities:
                enabled = sum(1 for e in tool_entities if e.enabled)
                lines.append(f"工具:         {enabled}/{len(tool_entities)} 已启用")
                by_source: Dict[str, int] = {}
                for e in tool_entities:
                    by_source[e.source] = by_source.get(e.source, 0) + 1
                for src, cnt in by_source.items():
                    lines.append(f"  [{src}]:     {cnt}")

            lines.append(f"角色提示词:    {len(rt.char.personality)} 条")
            lines.append(f"短期记忆:      {len(rt.mind.pfc.temporary)} 条")
        except Exception as e:
            lines.append(f"获取组件信息失败: {e}")
        return lines

    def get_event_stats(self) -> Optional[Dict[str, int]]:
        """获取事件总线触发统计。"""
        try:
            from core.event_bus import event_bus
            return event_bus.get_stats()
        except Exception as e:
            log(f"获取事件统计失败: {e}", "DEBUG")
            return None

    # ------------------------------------------------------------------
    # Mind 参数配置
    # ------------------------------------------------------------------

    def get_pfc_snapshot(self) -> Optional[Dict[str, Any]]:
        """返回 PFC 工作记忆状态快照。"""
        rt = get_runtime()
        if rt is None:
            return None
        try:
            return rt.mind.pfc.get_status_snapshot()
        except Exception as e:
            log(f"获取 PFC 快照失败: {e}", "DEBUG")
            return None

    def get_mind_config(self) -> Optional[Dict[str, Any]]:
        """读取当前 Mind 配置。"""
        try:
            from agent.config import get_config_provider
            mc = get_config_provider().mind
            return {
                "heartbeat_interval": mc.heartbeat_interval,
                "meta_decision_temperature": mc.meta_decision_temperature,
                "conversation_analysis_threshold": mc.conversation_analysis_threshold,
                "max_tool_iterations": mc.max_tool_iterations,
                "log_ai_output": mc.log_ai_output,
                "send_interim_text": mc.send_interim_text,
                "vector_search_batch_size": mc.vector_search_batch_size,
                "memory_recall_top_k": mc.memory_recall_top_k,
                "memory_recall_min_score": mc.memory_recall_min_score,
                "memory_time_decay_days": mc.memory_time_decay_days,
                "memory_warn_threshold": mc.memory_warn_threshold,
                "memory_max_per_type": mc.memory_max_per_type,
                "heartbeat_max_entries": mc.heartbeat_max_entries,
                "auto_consolidate_enabled": mc.auto_consolidate_enabled,
                "short_term_memory_size": mc.short_term_memory_size,
                "tool_recall_top_n": mc.tool_recall_top_n,
                "llm_timeout": mc.llm_timeout,
                "llm_max_retries": mc.llm_max_retries,
                "tool_system_rules": mc.tool_system_rules if hasattr(mc, "tool_system_rules") else [],
                "cross_channel_enabled": mc.cross_channel_enabled,
                "cross_channel_window_minutes": mc.cross_channel_window_minutes,
                "cross_channel_recall_min_score": mc.cross_channel_recall_min_score,
                "cross_channel_recall_max_results": mc.cross_channel_recall_max_results,
                "cross_channel_recall_scan_limit": mc.cross_channel_recall_scan_limit,
                "cross_channel_narrative_max_items": mc.cross_channel_narrative_max_items,
                "reasoning_effort": mc.reasoning_effort,
            }
        except Exception as e:
            log(f"获取 Mind 配置失败: {e}", "DEBUG")
            return None

    def save_mind_config(self, params: Dict[str, Any]) -> None:
        """保存 Mind 配置参数。"""
        from agent.config import get_config_provider
        get_config_provider().save_mind_config(**params)
