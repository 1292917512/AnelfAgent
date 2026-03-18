"""内置反思单元与配置驱动单元。"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from agent.messages import EntityData, MessageAssistant
from agent.memory.memory_types import MemoryEntry, MemoryType

from core.log import log

from .introspection_unit import (
    IntrospectionContext,
    IntrospectionResult,
    IntrospectionUnit,
    UnitMode,
    UnitScope,
)


def _clean_llm_output(text: str) -> str:
    """清洗 LLM 输出：移除思维链标签和模型特定 XML 标签。"""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    text = re.sub(r"</?(?:minimax|invoke|parameter)[^>]*>", "", text)
    return text.strip()


def _build_prompt_message(unit_name: str, prompt: str) -> Dict[str, str]:
    return {"role": "user", "content": f"[系统反思 - {unit_name}]\n{prompt}"}


# ======================================================================
# 配置驱动的通用单元（反思 / 任务共用）
# ======================================================================

_SCOPE_MAP: Dict[str, UnitScope] = {
    "global": UnitScope.GLOBAL,
    "entity": UnitScope.ENTITY,
    "any": UnitScope.ANY,
}

_MODE_MAP: Dict[str, UnitMode] = {
    "reflect": UnitMode.REFLECT,
    "task": UnitMode.TASK,
}

_MEMORY_TYPE_MAP: Dict[str, MemoryType] = {
    "reflection": MemoryType.REFLECTION,
    "semantic": MemoryType.SEMANTIC,
    "entity": MemoryType.ENTITY,
}


class PromptBasedUnit(IntrospectionUnit):
    """基于 JSON 配置的提示词驱动单元（反思和任务共用）。

    JSON 文件放在 config/introspection_units/（反思）或 config/tasks/（任务）。
    任务单元通过 mode=task 标识，可通过 tool_tags 指定专属工具集。
    """

    def __init__(
        self,
        name: str,
        prompt: str,
        scope: UnitScope = UnitScope.GLOBAL,
        mode: UnitMode = UnitMode.REFLECT,
        description: str = "",
        display_name: str = "",
        memory_type: MemoryType = MemoryType.REFLECTION,
        importance: float = 0.5,
        tags: Optional[List[str]] = None,
        source: str = "",
        null_keywords: Optional[List[str]] = None,
        enabled: bool = True,
        tool_tags: Optional[List[str]] = None,
    ) -> None:
        self.name = name
        self.default_prompt = prompt
        self.scope = scope
        self.mode = mode
        self.description = description
        self.display_name = display_name or name
        self.enabled = enabled
        self.tool_tags = tool_tags or []
        self._memory_type = memory_type
        self._importance = importance
        self._tags: List[str] = tags or []
        self._source = source or name
        self._null_keywords: List[str] = null_keywords or []

    @classmethod
    def from_dict(cls, data: Dict[str, Any], *, force_mode: Optional[UnitMode] = None) -> "PromptBasedUnit":
        """从字典（JSON 数据）构建实例。force_mode 可强制覆盖 mode 字段。"""
        name = data["name"]
        scope = _SCOPE_MAP.get(data.get("scope", "global"), UnitScope.GLOBAL)
        mode = force_mode or _MODE_MAP.get(data.get("mode", "reflect"), UnitMode.REFLECT)
        memory_type = _MEMORY_TYPE_MAP.get(
            data.get("memory_type", "reflection"), MemoryType.REFLECTION
        )
        return cls(
            name=name,
            prompt=data.get("prompt", ""),
            scope=scope,
            mode=mode,
            description=data.get("description", ""),
            display_name=data.get("display_name", name),
            memory_type=memory_type,
            importance=float(data.get("importance", 0.5)),
            tags=list(data.get("tags", [])),
            source=data.get("source", name),
            null_keywords=list(data.get("null_keywords", [])),
            enabled=bool(data.get("enabled", True)),
            tool_tags=list(data.get("tool_tags", [])),
        )

    def to_dict(self) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            "name": self.name,
            "display_name": self.display_name,
            "description": self.description,
            "scope": self.scope.value,
            "mode": self.mode.value,
            "enabled": self.enabled,
            "memory_type": self._memory_type.value,
            "importance": self._importance,
            "tags": self._tags,
            "source": self._source,
            "null_keywords": self._null_keywords,
            "prompt": self.default_prompt,
        }
        if self.tool_tags:
            result["tool_tags"] = self.tool_tags
        return result

    async def execute(self, ctx: IntrospectionContext) -> Optional[IntrospectionResult]:
        prompt = self.get_prompt(ctx)
        if not prompt:
            log(f"{self.name}: prompt 为空，跳过", "WARNING", tag="内省")
            return None

        content = await self._run_llm_reflection(ctx, prompt)
        if not content:
            log(f"{self.name}: 无产出", tag="内省")
            return None

        for kw in self._null_keywords:
            if kw in content:
                log(f"{self.name}: 匹配空响应关键词 [{kw}]，跳过", tag="内省")
                return None

        log(f"{self.name} 产出: {content[:80]}", tag="内省")
        return IntrospectionResult(
            unit_name=self.name,
            content=content,
            memory_type=self._memory_type,
            source=self._source,
            tags=list(self._tags),
            importance=self._importance,
        )


# ======================================================================
# 内置反思单元
# ======================================================================


class SelfReflectionUnit(IntrospectionUnit):
    """自我反思：回顾表现、记录要点、发现问题、制定计划。"""

    name = "self_reflection"
    scope = UnitScope.ANY
    description = "回顾近期表现，记录要点，发现问题，制定计划"
    default_prompt = (
        "根据记忆和对话内容，进行全面自我反思。请包含以下维度：\n"
        "1. [记忆] 记下重要的人和事，区分不同人的信息；用 memorize 工具保存尚未记录的关键内容\n"
        "2. [自检] 回顾自己最近的表现：回复质量如何？有没有犯错？工具使用是否恰当？\n"
        "3. [发现] 有什么需要改进或关注的问题？\n"
        "4. [计划] 接下来应该做什么？有没有未完成的承诺？\n"
        "可用工具（按需使用）：\n"
        "- 记忆：recall（语义搜索）、memorize（保存新记忆）\n"
        "- 便签导航：list_memory_files（列出文件）、view_memory_outline（查看标题大纲）\n"
        "- 便签读取：read_notes（主便签全文）、read_section（按标题读段落）\n"
        "- 便签编辑：write_section（替换/新建段落）、delete_section（删除段落）、"
        "patch_memory_file（字符串替换）、append_memory_file（追加内容）\n"
        "- 目标管理：create_goal/list_goals/update_goal/delete_goal\n"
        "编辑便签时优先使用 write_section/delete_section/patch_memory_file 精确修改，"
        "避免用 write_notes 整体覆写。\n"
        "5. [跨频道] 使用 list_conversations 浏览所有活跃会话，跨频道对比：\n"
        "   - 不同频道是否有人讨论相同话题？用 memorize 记录关联\n"
        "   - 同一用户是否在多个频道出现？可使用 link_entity 关联身份\n"
        "   - 是否有跨频道需要同步的信息或未完成的承诺？\n"
        "记忆库整理和合并由专门的整理单元负责，本次反思专注于自检和计划。\n"
        "完成工具操作后，输出结构化 Markdown 形式的反思总结。"
    )

    async def execute(self, ctx: IntrospectionContext) -> Optional[IntrospectionResult]:
        log("开始自我反思...", tag="内省")

        prompt = self.get_prompt(ctx)
        if not prompt:
            log("自我反思 prompt 为空，跳过", "WARNING", tag="内省")
            return None

        if ctx.active_channel_scopes:
            channel_info = "; ".join(
                f"{ch}: {', '.join(scopes)}"
                for ch, scopes in ctx.active_channel_scopes.items()
            )
            prompt += f"\n\n当前各频道活跃会话: {channel_info}"

        await self._emit_phase("context_build")
        if ctx.entity:
            conversation_list = await ctx.mind.get_conversation(ctx.entity)
        else:
            conversation_list = ctx.conversation_list

        base_messages = await ctx.mind.get_recollection(conversation_list)
        reflect_msg = _build_prompt_message(self.name, prompt)
        messages = list(base_messages) + [reflect_msg]

        await self._emit_phase("llm_start")
        content = await ctx.mind.reflect(
            messages,
            options={"temperature": ctx.config.analysis_temperature},
        )
        content = _clean_llm_output(content)
        if not content:
            log("自我反思无产出", tag="内省")
            await self._emit_phase("llm_end", content_preview="（无产出）")
            return None

        await self._emit_phase("llm_end", content_preview=content[:120])
        log(f"自我反思产出: {content[:80]}", tag="内省")

        source = "reflect_global"
        tags = ["type:reflection"]
        if ctx.entity:
            entity_id = str(ctx.entity.uid or ctx.entity.group_id)
            source = f"reflect_{entity_id}"
            scope_tag = f"user:{entity_id}" if ctx.entity.uid else f"group:{entity_id}"
            tags.insert(0, scope_tag)

        return IntrospectionResult(
            unit_name=self.name,
            content=content,
            memory_type=MemoryType.REFLECTION,
            source=source,
            tags=tags,
            importance=0.7,
        )


class EntityAnalysisUnit(IntrospectionUnit):
    """实体画像分析：用户/群组结构化总结并更新画像。"""

    name = "entity_analysis"
    scope = UnitScope.ENTITY
    description = "对用户或群组进行结构化画像总结"
    default_prompt = (
        "请对 {entity} 进行画像分析并输出结构化 Markdown 总结。\n\n"
        "## 分析要求\n"
        "1. 仔细阅读对话历史和已有画像（如有），提取关键信息\n"
        "2. 可使用工具辅助分析（recall 检索相关记忆、get_conversation 查看完整对话）\n"
        "3. **增量更新**：保留已有画像中仍然准确的信息，补充新发现，修正过时内容\n"
        "4. 输出将覆盖旧画像，务必确保完整性——不要遗漏旧画像中仍有效的信息\n\n"
        "## 用户画像模板（当 {entity} 为用户时使用）\n"
        "```\n"
        "## 基本信息\n"
        "- 名称/昵称：（从对话中提取的称呼）\n"
        "- 身份标识：{entity}\n"
        "## 性格印象\n"
        "（说话风格、性格特点、行为模式）\n"
        "## 兴趣爱好\n"
        "（话题偏好、关注领域）\n"
        "## 关系与互动风格\n"
        "（与我的关系、互动特点、称呼习惯）\n"
        "## 重要事件\n"
        "（值得记住的对话内容、承诺、约定）\n"
        "## 注意事项\n"
        "（需要特别留意的偏好或禁忌）\n"
        "```\n\n"
        "## 群组画像模板（当 {entity} 为群组时使用）\n"
        "```\n"
        "## 群组概况\n"
        "- 群组标识：{entity}\n"
        "- 群组定位/主题：\n"
        "## 活跃成员\n"
        "（列出主要成员及其特点）\n"
        "## 群组氛围\n"
        "（交流风格、群内文化）\n"
        "## 重要事件\n"
        "（群内发生的关键事件）\n"
        "## 注意事项\n"
        "（群规、敏感话题等）\n"
        "```\n\n"
        "完成工具操作后，直接输出画像内容（纯 Markdown，不要包裹在代码块中）。"
    )

    async def execute(self, ctx: IntrospectionContext) -> Optional[IntrospectionResult]:
        entity = ctx.entity
        if entity is None:
            return None

        desc = entity.get_entity_desc()
        log(f"实体分析中: {desc}", tag="内省")

        prompt = self.get_prompt(ctx)
        if not prompt:
            log("实体分析 prompt 为空，跳过", "WARNING", tag="内省")
            return None
        prompt = prompt.replace("{entity}", desc)

        await self._emit_phase("context_build", entity=desc)
        entity_conversation = await ctx.mind.get_conversation(entity)
        user_query_entity = MessageAssistant(uid=entity.uid or 0)
        user_conversation = await ctx.mind.get_conversation(user_query_entity)
        combined = entity_conversation + user_conversation

        alias_convs = await self._collect_alias_conversations(ctx, entity)
        if alias_convs:
            combined = combined + alias_convs

        base_messages = await ctx.mind.get_recollection(combined)
        personality_desc = entity.get_personality_desc()
        analysis_messages = list(base_messages)
        if personality_desc:
            analysis_messages.append(personality_desc)
        analysis_messages.append(_build_prompt_message(self.name, prompt))

        await self._emit_phase("llm_start", entity=desc)
        content = await ctx.mind.reflect(
            analysis_messages,
            options={"temperature": ctx.config.analysis_temperature},
        )
        content = _clean_llm_output(content)
        if not content:
            log(f"实体分析无产出: {desc}", tag="内省")
            await self._emit_phase("llm_end", entity=desc, content_preview="（无产出）")
            return None

        await self._emit_phase("llm_end", entity=desc, content_preview=content[:120])

        await self._emit_phase("storing", entity=desc)
        entity.set_personality(content)
        await ctx.mind.everything_data.save_entity_personality(entity)
        log(f"实体画像更新: {desc} -> {content[:80]}", tag="内省")

        entity_id = str(entity.uid or entity.group_id)
        source = f"entity_{entity_id}"
        scope_tag = f"user:{entity_id}" if entity.uid else f"group:{entity_id}"

        if ctx.mind.memory_store:
            old_entries = await ctx.mind.memory_store.list_recent(
                limit=5, memory_type=MemoryType.ENTITY, source=source,
            )
            for old in old_entries:
                if old.id:
                    await ctx.mind.memory_store.delete(old.id)

        return IntrospectionResult(
            unit_name=self.name,
            content=content,
            memory_type=MemoryType.ENTITY,
            source=source,
            tags=[scope_tag, "type:profile"],
            importance=0.8,
        )

    @staticmethod
    async def _collect_alias_conversations(
        ctx: "IntrospectionContext",
        entity: "EntityData",
    ) -> list[dict]:
        """收集所有 alias 关联身份的对话记录（不含自身）。"""
        try:
            sqlite = ctx.mind.everything_data.router.sqlite
            scope_type = "user" if entity.uid and entity.uid not in (0, "0") else "group"
            scope_id = str(entity.uid) if scope_type == "user" else str(entity.group_id)

            primary = await sqlite.resolve_alias(scope_type, scope_id)
            p_type, p_id = primary if primary else (scope_type, scope_id)

            aliases = await sqlite.get_aliases_for_primary(p_type, p_id)
            all_identities = [(p_type, p_id)] + [(a["scope_type"], a["scope_id"]) for a in aliases]
            current = (scope_type, scope_id)

            extra_conv: list[dict] = []
            for id_type, id_id in all_identities:
                if (id_type, id_id) == current:
                    continue
                alias_entity = MessageAssistant(
                    uid=id_id if id_type == "user" else 0,
                    group_id=id_id if id_type == "group" else 0,
                )
                conv = await ctx.mind.get_conversation(alias_entity)
                if conv:
                    extra_conv.extend(conv)
                    log(f"alias 对话合并: {id_type}:{id_id} ({len(conv)} 条)", "DEBUG", tag="内省")
            return extra_conv
        except Exception as exc:
            log(f"alias 对话收集失败: {exc}", "WARNING", tag="内省")
            return []


class MemoryHealthUnit(IntrospectionUnit):
    """记忆健康检查：纯逻辑检查记忆阈值，不调用 LLM。"""

    name = "memory_health"
    scope = UnitScope.GLOBAL
    description = "检查记忆数量阈值，输出整理建议"

    def should_run(self, ctx: IntrospectionContext) -> bool:
        if ctx.memory_warnings_checked:
            return False
        return super().should_run(ctx)

    async def execute(self, ctx: IntrospectionContext) -> Optional[IntrospectionResult]:
        warnings = await self._check(ctx)
        if not warnings:
            return None
        content = "\n".join(f"- {w}" for w in warnings)
        return IntrospectionResult(
            unit_name=self.name,
            content=content,
            memory_type=MemoryType.REFLECTION,
            source="memory_health",
            tags=["type:health_check"],
            importance=0.3,
        )

    async def _check(self, ctx: IntrospectionContext) -> List[str]:
        if not ctx.mind.memory_store:
            return []

        unit_cfg = ctx.config.get_unit(self.name)
        warn_threshold = unit_cfg.params.get("memory_warn_threshold", 200)
        entity_merge_threshold = unit_cfg.params.get("entity_merge_threshold", 5)
        reflection_merge_threshold = unit_cfg.params.get("reflection_merge_threshold", 10)

        warnings: List[str] = []
        try:
            type_counts = await ctx.mind.memory_store.get_type_counts()

            entity_count = type_counts.get("entity", 0)
            if entity_count > entity_merge_threshold:
                warnings.append(
                    f"实体记忆有 {entity_count} 条（阈值 {entity_merge_threshold}），"
                    "建议使用 memory_deep_search 查看并用 merge_memories 合并同一实体的画像记忆"
                )

            reflection_count = type_counts.get("reflection", 0)
            if reflection_count > reflection_merge_threshold:
                warnings.append(
                    f"反思记忆有 {reflection_count} 条（阈值 {reflection_merge_threshold}），"
                    "建议使用 memory_deep_search 查看并用 merge_memories 合并相似的反思记忆"
                )

            for mem_type, count in type_counts.items():
                if mem_type in ("entity", "reflection"):
                    continue
                if count > warn_threshold:
                    warnings.append(
                        f"{mem_type} 记忆有 {count} 条（阈值 {warn_threshold}），建议整理和合并"
                    )
        except Exception as exc:
            log(f"记忆阈值检查异常: {exc}", "WARNING", tag="内省")

        if warnings:
            log(f"记忆阈值预警: {len(warnings)} 条", tag="内省")
        return warnings
