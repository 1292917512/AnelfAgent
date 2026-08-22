"""PrefrontalCortex — AI 工作记忆中枢（组合门面）。

管理短期记忆、任务队列、工具召回、频道感知和态势上下文。
LLM 每轮思考从此处获取完整工作记忆（工具提示、频道能力、热工具等）。

实现已按职责拆分为三个自包含组件（本类仅做构造接线与方法委托，
公开 API 签名保持与拆分前一致，消费方无感知）：

- ``agent.mind.work_memory.WorkMemory``：消息队列 / 待办持久化 / 短期记忆 / 媒体暂存 / 态势路由
- ``agent.mind.tool_assembly.ToolAssembly``：工具召回 / tag 激活 / 动态发现 / schema 合并与门控
- ``agent.mind.context_assembly.ContextAssembly``：系统提示构建 / Prompt 分层缓存 / 执行上下文
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple, Union

from agent.llm.types import ImageContent
from agent.messages import EntityData, Everything
from agent.mind.autonomous import MindTask
from agent.mind.context_assembly import (
    ContextAssembly,
    _env_info_block,  # noqa: F401  # re-export（测试/外部兼容）
    _safe_entity_scope,  # noqa: F401  # re-export（round_helpers 等引用）
)
from agent.mind.tool_assembly import ToolAssembly
from agent.mind.work_memory import WorkMemory
from agent.storage.data_center import EverythingData
from agent.utils.unique_queue import UniqueQueue

if TYPE_CHECKING:
    from agent.channel.manager import ChannelManager
    from agent.storage.data_center import ConversationData


class PrefrontalCortex:
    """工作记忆门面：构造接线 + 方法委托（公开 API 与拆分前一致）。"""

    def __init__(
            self,
            everything_data: EverythingData,
            channel_manager: Optional["ChannelManager"] = None,
            conversation_data: Optional["ConversationData"] = None,
    ) -> None:
        self.record: dict[str, int] = {}
        self.everything_data = everything_data
        self._channel_manager = channel_manager
        self._conversation_data = conversation_data

        self.work_memory = WorkMemory(everything_data, conversation_data)
        self.tool_assembly = ToolAssembly(channel_manager)
        self.context_assembly = ContextAssembly(
            self.work_memory, self.tool_assembly, channel_manager, conversation_data,
        )
        # 消息标签扫描命中时经 ToolAssembly 激活工具（数据面 → 工具面接线）
        self.work_memory.tool_assembly = self.tool_assembly

    # ==================================================================
    # 队列直通属性（消费方直接访问队列对象：assistant/cycle/scheduler）
    # ==================================================================

    @property
    def pending_user(self) -> UniqueQueue[str]:
        return self.work_memory.pending_user

    @property
    def pending_group(self) -> UniqueQueue[str]:
        return self.work_memory.pending_group

    @property
    def pending_analysis(self) -> UniqueQueue:
        return self.work_memory.pending_analysis

    @property
    def temporary(self) -> list[Dict]:
        """全桶展开视图（管理接口/监控用；LLM 上下文请用 get_temporary(scope)）。"""
        return self.work_memory.temporary

    @property
    def tools_version(self) -> int:
        """动态工具集版本号（tag 激活/动态发现变化时递增）。"""
        return self.tool_assembly.tools_version

    # ==================================================================
    # WorkMemory 委托
    # ==================================================================

    async def add_task(self, anything: Everything) -> None:
        await self.work_memory.add_task(anything)

    def restore_persisted_tasks(self, rows: List[Dict[str, Any]]) -> int:
        return self.work_memory.restore_persisted_tasks(rows)

    def requeue_analysis(
        self, group_id: Union[int, str], uid: Union[int, str], adapter_key: str = ""
    ) -> None:
        self.work_memory.requeue_analysis(group_id, uid, adapter_key)

    async def pop_user_task(self) -> Optional[str]:
        return await self.work_memory.pop_user_task()

    async def pop_group_task(self) -> Optional[str]:
        return await self.work_memory.pop_group_task()

    async def pop_analysis_task(self) -> Optional[EntityData]:
        return await self.work_memory.pop_analysis_task()

    def add_general_task(self, task: MindTask) -> None:
        self.work_memory.add_general_task(task)

    def peek_general_tasks(self) -> list[MindTask]:
        return self.work_memory.peek_general_tasks()

    def get_pending_message_previews(self) -> Dict[str, Tuple[str, str]]:
        return self.work_memory.get_pending_message_previews()

    def consume_general_task(self, index: int) -> bool:
        return self.work_memory.consume_general_task(index)

    def clear_general_tasks(self) -> int:
        return self.work_memory.clear_general_tasks()

    def clear_general_tasks_before(self, snapshot_count: int) -> int:
        return self.work_memory.clear_general_tasks_before(snapshot_count)

    def peek_all_tasks(self) -> List[Tuple[str, str, str, str]]:
        return self.work_memory.peek_all_tasks()

    def consume_scope_task(self, scope: str) -> bool:
        return self.work_memory.consume_scope_task(scope)

    def get_unread_count(self, scope: str) -> int:
        return self.work_memory.get_unread_count(scope)

    def has_pending_tasks(self) -> bool:
        return self.work_memory.has_pending_tasks()

    def set_adapter_key(self, scope: str, adapter_key: str) -> None:
        self.work_memory.set_adapter_key(scope, adapter_key)

    def get_adapter_key(self, scope: str) -> str:
        return self.work_memory.get_adapter_key(scope)

    def set_message_preview(self, scope: str, preview: str) -> None:
        self.work_memory.set_message_preview(scope, preview)

    def add_temporary(self, temporary_clip: Dict, scope: str = "") -> None:
        self.work_memory.add_temporary(temporary_clip, scope)

    def get_temporary(self, scope: str = "") -> list[Dict]:
        return self.work_memory.get_temporary(scope)

    def delete_temporary(self, index: int) -> bool:
        return self.work_memory.delete_temporary(index)

    def clear_temporary(self) -> int:
        return self.work_memory.clear_temporary()

    def delete_temporary_in_scope(self, scope: str, index: int) -> bool:
        return self.work_memory.delete_temporary_in_scope(scope, index)

    def clear_temporary_in_scope(self, scope: str) -> int:
        return self.work_memory.clear_temporary_in_scope(scope)

    def collect_images(self, scope: str = "") -> List[ImageContent]:
        return self.work_memory.collect_images(scope)

    def collect_media(self, scope: str = "") -> list:
        return self.work_memory.collect_media(scope)

    # ==================================================================
    # ToolAssembly 委托
    # ==================================================================

    def record_tool_use(self, tool_name: str) -> None:
        self.tool_assembly.record_tool_use(tool_name)

    def get_tool_use_total(self) -> int:
        return self.tool_assembly.get_tool_use_total()

    def get_hot_tool_names(self) -> list[str]:
        return self.tool_assembly.get_hot_tool_names()

    def get_hot_tool_schemas(self) -> list[dict]:
        return self.tool_assembly.get_hot_tool_schemas()

    def get_channel_tool_schemas(self, adapter_key: str) -> list[dict]:
        return self.tool_assembly.get_channel_tool_schemas(adapter_key)

    def resolve_tag_tool_schemas(self) -> list[dict]:
        return self.tool_assembly.resolve_tag_tool_schemas()

    def activate_media_tools(self, images: list, media_segments: list) -> None:
        self.tool_assembly.activate_media_tools(images, media_segments)

    def expand_discovered_tools(self, tool_calls: list) -> None:
        self.tool_assembly.expand_discovered_tools(tool_calls)

    def clear_dynamic_tools(self, scope: str = "") -> None:
        self.tool_assembly.clear_dynamic_tools(scope)

    async def get_active_tool_schemas(self, adapter_key: str = "", scope: str = "") -> list[dict]:
        return await self.tool_assembly.get_active_tool_schemas(adapter_key, scope)

    # ==================================================================
    # ContextAssembly 委托
    # ==================================================================

    def build_tool_system_prompt(
            self,
            models_summary: str = "",
            adapter_key: str = "",
            target_id: str = "",
            direct_vision: bool = False,
    ) -> list[dict]:
        return self.context_assembly.build_tool_system_prompt(
            models_summary=models_summary,
            adapter_key=adapter_key,
            target_id=target_id,
            direct_vision=direct_vision,
        )

    def build_stable_layer(
            self,
            persona_parts: List[str],
            models_summary: str = "",
            direct_vision: bool = False,
            static_guide: str = "",
    ) -> str:
        return self.context_assembly.build_stable_layer(
            persona_parts,
            models_summary=models_summary,
            direct_vision=direct_vision,
            static_guide=static_guide,
        )

    def stable_fingerprint(self, models_summary: str = "", direct_vision: bool = False) -> str:
        return self.context_assembly.stable_fingerprint(models_summary, direct_vision)

    async def build_llm_context(self, **kwargs: Any) -> List[Dict]:
        return await self.context_assembly.build_llm_context(**kwargs)

    def build_execution_context(
            self,
            execution_steps: list[str],
            start_time: float,
            iteration: int,
            *,
            adapter_key: str = "",
            safety_limit: int = 0,
            anything: Optional["Everything"] = None,
            budget_hint: str = "",
    ) -> dict:
        return self.context_assembly.build_execution_context(
            execution_steps, start_time, iteration,
            adapter_key=adapter_key, safety_limit=safety_limit, anything=anything,
            budget_hint=budget_hint,
        )

    @staticmethod
    def _build_media_rules(direct_vision: bool = False) -> str:
        return ContextAssembly._build_media_rules(direct_vision)

    # ==================================================================
    # 管理与监控接口
    # ==================================================================

    def get_entity_list(self) -> List[Dict]:
        result: List[Dict] = []
        for key, entity in self.everything_data.entities.items():
            result.append({
                "key": key,
                "uid": entity.uid,
                "group_id": entity.group_id,
                "personality": entity.personality,
            })
        return result

    def get_status_snapshot(self) -> Dict:
        """返回 PFC 完整状态快照（供 Web 监控）。"""
        pending_msgs = []
        for scope, uid, group_id, preview in self.work_memory.peek_all_tasks():
            adapter_key = self.work_memory.get_adapter_key(scope)
            pending_msgs.append({
                "scope": scope, "uid": uid, "group_id": group_id,
                "preview": preview, "adapter_key": adapter_key,
            })

        general_tasks = []
        for t in self.work_memory.peek_general_tasks():
            general_tasks.append({
                "type": t.task_type.value, "scope": t.scope, "preview": t.preview,
            })

        try:
            from agent.mind.prompt_layers import prompt_cache_manager
            cache_stats = prompt_cache_manager.stats()
        except Exception:
            cache_stats = {}

        try:
            from agent.mind.cache_stats import cache_usage_tracker
            provider_cache = cache_usage_tracker.summary()
        except Exception:
            provider_cache = {}

        return {
            "tool_recall": [
                {"name": n, "count": c} for n, c in self.tool_assembly.get_tool_recall_sorted()
            ],
            "tool_recall_top_n": self.tool_assembly.tool_recall_top_n,
            "tag_activated_tools": sorted(self.tool_assembly.tag_activated_tools),
            "discovered_tools": sorted(self.tool_assembly.discovered_tools),
            "pending_messages": pending_msgs,
            "general_tasks": general_tasks,
            "pending_analysis_count": len(self.work_memory.pending_analysis),
            "short_term_memory_count": len(self.work_memory.temporary),
            "short_term_memory_max": self.work_memory.max_temp,
            "prompt_cache": cache_stats,
            "provider_cache": provider_cache,
        }
