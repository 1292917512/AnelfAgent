"""上下文材料的分层、预算与文件缓存。

模型可见：沿用人设、工具目录、召回来源；资料被裁剪时在动态区说明边界与取回入口。
Token：画像和检索共享既有上限，省略提示约百字；不另起模型调用。
缓存：沿用各层指纹、缓存键与消息顺序，不改稳定前缀。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

from agent.context.inputs import RecollectionInputs
from agent.context.prompt_layers import FileLayerCache
from agent.llm.llm_client import LLMClient
from agent.memory.notes import (
    build_dynamic_notes,
    build_file_index_block,
    build_heartbeat_status_block,
    build_memory_status_block,
    build_notes_empty_hint,
    build_static_guide,
    get_memory_dir,
    get_notes_path,
)
from core.activity import EVENT_ACTIVITY_CONTEXT
from core.config import get_config_int
from core.event_bus import EVENT_THINKING_CONTEXT_BUILD, event_bus

if TYPE_CHECKING:
    from agent.context.assembly import ContextAssembly
    from agent.llm import ChatModel
    from agent.llm.llm_manager import LLMManager
    from agent.messages import Everything


class ContextPreparation:
    """持有文件缓存，使用上下文组装器构建层次化消息。"""

    def __init__(self, assembly: ContextAssembly) -> None:
        self.assembly = assembly
        self._file_cache = FileLayerCache()

    async def build_layers(
            self,
            persona_parts: List[str],
            scope: str,
            models_summary: str,
            permanent_text: str = "",
            *,
            lean: bool = False,
            direct_vision: bool = False,
    ) -> Tuple[str, str, str, bool, bool, bool, str, str]:
        """构建 stable 人设块 / stable 工具块 / context 层三段提示（经 PromptCacheManager 缓存复用）。

        人设块：人设 + 运行环境 + 静态指南——与工具无关，指纹不含工具版本因子，
        工具激活/发现/清理不会使其失效，长期命中（缓存前缀中最大最稳定的段）。
        工具块：工具目录 + 使用规则 + 媒体规则——随工具集变化经 stable_fingerprint 重建。
        context 层：动态便签 + 永久记忆块 + 文件索引，仅编辑/永久记忆增删时重建；
        lean（任务精简）模式只含永久记忆块，且状态区块留空。

        文件型层通过 FileLayerCache 做 mtime O(1) 快检，未变时跳过 I/O。

        Returns:
            (persona_text, tools_text, context_text, persona_hit, tools_hit,
             context_hit, status_text, heartbeat_text)
        """
        # --- stable 人设块：人设 + 环境 + 静态指南（指纹不含工具因子） ---
        from agent.context.assembly import _env_info_block
        from agent.context.prompt_layers import (
            LAYER_CONTEXT,
            LAYER_STABLE_PERSONA,
            LAYER_STABLE_TOOLS,
            prompt_cache_manager,
        )
        notes_path = get_notes_path()
        static_guide, _ = self._file_cache.get_or_load(notes_path, build_static_guide)
        persona_hash = prompt_cache_manager.compute_hash(
            *persona_parts, static_guide, _env_info_block(),
        )
        persona_text, persona_hit = prompt_cache_manager.get_or_build(
            scope, LAYER_STABLE_PERSONA, persona_hash,
            lambda: self.assembly.build_persona_block(persona_parts, static_guide),
        )

        # --- stable 工具块：工具目录 + 规则（指纹含工具版本门控） ---
        tools_hash = prompt_cache_manager.compute_hash(
            self.assembly.stable_fingerprint(models_summary, direct_vision),
        )
        tools_text, tools_hit = prompt_cache_manager.get_or_build(
            scope, LAYER_STABLE_TOOLS, tools_hash,
            lambda: self.assembly.build_tools_block(models_summary, direct_vision),
        )

        # --- context 层：动态便签 + 文件索引（lean 模式只留永久记忆块） ---
        if lean:
            status_text = ""
            heartbeat_text = ""
            context_parts = [p for p in (permanent_text,) if p]
        else:
            dynamic_notes, _ = self._file_cache.get_or_load(notes_path, build_dynamic_notes)
            file_index, _ = self._file_cache.get_or_load(get_memory_dir(), build_file_index_block)
            # 记忆状态区块（心跳维护，周期性变化）不入 context 层，尾部动态区独立注入
            status_text, _ = self._file_cache.get_or_load(notes_path, build_memory_status_block)
            # 心跳与任务态势区块同族处理（AUTO:heartbeat-status，尾部动态区独立注入）
            heartbeat_text, _ = self._file_cache.get_or_load(notes_path, build_heartbeat_status_block)
            context_parts = [p for p in (dynamic_notes, permanent_text, file_index) if p]
        if lean and not context_parts:
            # 任务精简模式无永久记忆时不注入空便签提示（任务用不到便签系统）
            return persona_text, tools_text, "", persona_hit, tools_hit, False, status_text, heartbeat_text
        if not context_parts:
            context_parts = [build_notes_empty_hint()]
        context_hash = prompt_cache_manager.compute_hash(*context_parts)
        context_text, context_hit = prompt_cache_manager.get_or_build(
            scope, LAYER_CONTEXT, context_hash,
            lambda: "[个人笔记/便签记忆]\n" + "\n\n".join(context_parts),
        )
        return persona_text, tools_text, context_text, persona_hit, tools_hit, context_hit, status_text, heartbeat_text


    async def build_recollection(
            self, inputs: RecollectionInputs, conversation_list: List[Dict],
            anything: Optional[Everything], *, persona_parts: List[str],
            models_summary: str, direct_vision: bool, lean: bool = False,
    ) -> List[Dict]:
        """对准备好的召回事实分层、裁剪、缓存并构建完整消息。"""
        entity_scope = anything.entity_scope if anything else ""
        profile_msgs = [dict(message) for message in inputs.profiles]
        memory_msgs, relation_msgs = list(inputs.memories), list(inputs.relations)
        allocation = None
        if lean:
            permanent_text = str(memory_msgs[0]["content"]) if memory_msgs else ""
            memory_msgs = []
        else:
            for position, block in enumerate(inputs.profile_blocks):
                if block:
                    profile_msgs.insert(position, {"role": "system", "content": block})
            # 与当前话题有关的跨频道历史召回
            memory_msgs = [{**msg, "_source": {"origin": "memory_recall"}} for msg in memory_msgs]
            memory_msgs.extend({**msg, "_source": {"origin": "cross_channel_recall"}} for msg in inputs.cross_channel)
            # 技能匹配注入（volatile 层）：当前对话语义匹配到的经验技能
            memory_msgs.extend(inputs.skills)

            # 永久记忆置顶块从每轮重建的会话区剥离：其内容字节稳定（仅 permanent
            # 增删时变化），移交 context 层走内容寻址缓存，不再每轮占用未缓存区段
            pin_msgs = [
                m for m in memory_msgs
                if str(m.get("content", "")).startswith("[系统注入·永久记忆]")
            ]
            if pin_msgs:
                memory_msgs = [
                    m for m in memory_msgs
                    if not str(m.get("content", "")).startswith("[系统注入·永久记忆]")
                ]
            permanent_text = str(pin_msgs[0]["content"]) if pin_msgs else ""

            allocation = allocate_recollection(profile_msgs, memory_msgs)
            profile_msgs, memory_msgs = allocation.profiles, list(allocation.evidence)
            notice = "\n".join(part for part in (inputs.recall_notice, allocation.omission_notice()) if part)
            if notice:
                memory_msgs.append({"role": "system", "content": notice,
                                    "_source": {"origin": "memory_policy"}})

        # Prompt 分层构建：
        # stable 人设块（人设+环境+静态指南）长期冻结，stable 工具块（目录+规则）随工具集重建，
        # context 层（便签）低频重建，volatile 层（语义召回等）每轮构建并置于其后，保证前缀缓存命中。
        persona_text, tools_text, context_text, persona_hit, tools_hit, context_hit, status_text, heartbeat_text = (
            await self.build_layers(persona_parts, entity_scope, models_summary, permanent_text, lean=lean, direct_vision=direct_vision)
        )

        await event_bus.emit(EVENT_THINKING_CONTEXT_BUILD, {
            "memory_msgs_count": len(memory_msgs),
            "stable_cache_hit": persona_hit and tools_hit,
            "context_cache_hit": context_hit,
        })

        messages = await self.assembly.build_llm_context(
            persona_text=persona_text,
            tools_text=tools_text,
            context_text=context_text,
            memory_msgs=memory_msgs,
            anything=anything,
            adapter_key=getattr(anything, "adapter_key", ""),
            models_summary=models_summary,
            prefetched_conversation=conversation_list,
            scope=entity_scope,
            profile_msgs=profile_msgs,
            relation_msgs=relation_msgs,
            summary_row=inputs.summary,
            status_text=status_text,
            heartbeat_text=heartbeat_text,
            repeat_hint_text=inputs.repeat_hint,
        )
        if allocation is not None:
            await event_bus.emit(EVENT_ACTIVITY_CONTEXT, {
                "status": "running", "recall": allocation.report(inputs.recall_level),
            })
        return messages


def model_context(client: ChatModel, manager: LLMManager | None) -> tuple[str, bool]:
    """读取可用模型目录及当前视觉能力，构建工具层所需的模型提示。"""
    summary = manager.get_models_summary() if manager else ""
    text = "# 可用模型\n" + summary + "\n不要编造工具不存在的功能或数据。" if summary else ""
    return text, bool(isinstance(client, LLMClient) and client.config.supports_vision)


_TRUNCATED = "\n（已截断）"


def apply_memory_budget(messages: list[dict], *, budget: int | None = None) -> list[dict]:
    """按已有相关性顺序保留块；标记占用预算，返回副本供组装器注入。"""
    remaining = max(0, get_config_int("memory_inject_max_chars", 6000) if budget is None else budget)
    result: list[dict] = []
    for message in messages:
        content = str(message.get("content", ""))
        if not content:
            continue
        if len(content) <= remaining:
            result.append(dict(message))
            remaining -= len(content)
        else:
            if remaining > 200:
                result.append({**message, "content": content[:remaining - len(_TRUNCATED)] + _TRUNCATED})
            break
    return result


@dataclass(frozen=True)
class BudgetGroup:
    prepared_blocks: int
    prepared_chars: int
    injected_blocks: int
    injected_chars: int
    truncated_blocks: int
    omitted_blocks: int


@dataclass
class RecallAllocation:
    profiles: list[dict]
    evidence: list[dict]
    budget: int
    profile_usage: BudgetGroup
    evidence_usage: BudgetGroup

    def report(self, level: str) -> dict[str, Any]:
        """仅输出计数，不复制正文或产生新的记忆事实。"""
        return {"level": level, "budget_chars": self.budget,
                "profiles": asdict(self.profile_usage), "evidence": asdict(self.evidence_usage)}

    def omission_notice(self) -> str:
        """存在省略时提醒模型证据边界，避免将摘录当成完整历史。"""
        groups = (self.profile_usage, self.evidence_usage)
        if not any(group.truncated_blocks or group.omitted_blocks for group in groups):
            return ""
        return ("[系统注入·资料范围] 画像或检索证据受本轮预算限制，部分内容已截断或省略，"
                "不代表完整历史。缺少任务进展、约束或出处时，请用 recall 或对应画像工具取回后再判断。")


def _usage(before: list[dict], after: list[dict]) -> BudgetGroup:
    available = [message for message in before if message.get("content")]
    return BudgetGroup(len(available), sum(len(str(m["content"])) for m in available),
                       len(after), sum(len(str(m["content"])) for m in after),
                       sum(m["content"] != original["content"] for m, original in zip(after, available, strict=False)),
                       len(available) - len(after))


def allocate_recollection(profiles: list[dict], evidence: list[dict]) -> RecallAllocation:
    """两类内容各有保留空间，未使用的配额让给另一类；不改变各自排序。"""
    budget = max(0, get_config_int("memory_inject_max_chars", 6000))
    profile_chars = sum(len(str(m.get("content", ""))) for m in profiles)
    evidence_chars = sum(len(str(m.get("content", ""))) for m in evidence)
    profile_quota = min(profile_chars, max(budget // 2, budget - evidence_chars))
    kept_profiles = apply_memory_budget(profiles, budget=profile_quota)
    used_profiles = sum(len(str(m["content"])) for m in kept_profiles)
    kept_evidence = apply_memory_budget(evidence, budget=budget - used_profiles)
    used_evidence = sum(len(str(m["content"])) for m in kept_evidence)
    if profile_chars > used_profiles and budget - used_evidence > used_profiles:
        kept_profiles = apply_memory_budget(profiles, budget=budget - used_evidence)
    return RecallAllocation(kept_profiles, kept_evidence, budget,
                            _usage(profiles, kept_profiles), _usage(evidence, kept_evidence))
