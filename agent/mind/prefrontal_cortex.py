"""PrefrontalCortex — AI 工作记忆中枢。

管理短期记忆、任务队列、工具召回、频道感知和态势上下文。
LLM 每轮思考从此处获取完整工作记忆（工具提示、频道能力、热工具等）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Dict, List, Optional, Tuple, Union

from agent.llm.types import ImageContent
from agent.messages import Everything, EverythingGroup, EntityData
from agent.mind.autonomous import MindTask, TaskType
from agent.utils.unique_queue import UniqueQueue
from agent.storage.data_center import EverythingData
from core.entity import EntityRegistry
from core.log import log
from core.tags import etag_all

if TYPE_CHECKING:
    from agent.channel.manager import ChannelManager
    from agent.storage.data_center import ConversationData


def _get_mind_config():
    from agent.config import get_mind_config
    return get_mind_config()


def _delegation_enabled() -> bool:
    """子代理委托是否启用（后台任务规范提示的注入条件）。"""
    from core.config import get_config_bool
    return get_config_bool("delegation_enabled", True)


# ==================================================================
# 提示词模板常量
# ==================================================================

# 工具使用指引（仅保留无法程序强制的引导性内容；
# "必须 function calling"由纯工具模式 API 强制，"失败勿重复"由工具守卫强制，
# 并行调用用法由 stable 层 _PARALLEL_CALL_HINT 统一提供，此处不重复）
_TOOL_USAGE_RULES = (
    "[工具使用指引]\n"
    "1. 如果返回“工具不存在/未知工具”，必须先调用 list_entity_methods 获取精确方法名，禁止继续猜测相似名称。\n"
    "2. 同一任务连续两次出现未知工具后，必须停止继续猜测并改用已确认可用工具，或直接结束并说明限制。"
)

_MEMORY_USAGE_HINT = (
    "[记忆使用提示]\n"
    "便签文件是索引，数据库是详细存储。两者通过标签联动。\n"
    "- 看到人物 UID → get_entity_profile 查完整画像\n"
    "- 想了解某话题 → recall 语义搜索 DB\n"
    "- 新信息 → memorize 存 DB（标签: type:/user:/group:/topic:），必要时更新便签索引\n"
    "- 工具出错 → recall_tool_errors 查历史错误\n"
    "- 整理记忆 → 先 view_memory_outline 看文件结构，按顶部分类标准写入"
)

_FINAL_ROUND_WARNING = (
    "⚠️ [最终轮次] 这是最后一轮机会，系统将在本轮后强制结束。"
    "请立即完成必要操作并调用 end_reply，不要再开新工具调用链。"
)

_URGENT_ROUND_WARNING = (
    "⚠️ [轮次告急] 仅剩 2 轮，请尽快收束操作并调用 end_reply。"
    "避免在此阶段开启复杂工具链。"
)

_NO_PENDING_HINT = "[当前无外部消息] 当前处于自主思考阶段，可执行工具操作或调用 end_reply 结束，必须使用工具"

_PARALLEL_CALL_HINT = (
    "# 并行工具调用\n"
    "同一轮可以发起多个工具调用（原生并行），参数已确定的独立操作应一次性全部发起，减少对话轮次。\n"
    "**回复完毕且没有其他操作时，必须在同一轮同时调用 send_message 和 end_reply，一次完成回复并结束，禁止分开调用。**\n"
    "**end_reply 会彻底结束本轮对话，不存在「下一轮再继续」——文字中声明要做的操作，"
    "必须在调用 end_reply 之前实际发起工具调用，只说不做等于放弃。**"
)

_BACKGROUND_TASK_HINT = (
    "# 后台任务\n"
    "delegate_task(background=true) 启动的后台任务，完成时系统会自动通知你（触发新一轮对话），无需守候。\n"
    "- 想查进度 → 调用 check_background_tasks\n"
    "- 想等结果 → send_message 告知用户后调用 end_reply，完成时你会被自动唤醒\n"
    "- 禁止反复输出「任务还在运行」之类的文字——这些文字用户完全看不到，且会被系统判定为内心独白"
)

_PENDING_HINT = "→ 处理消息或执行操作，空消息表示当前处于自主思考阶段，不是对方发送的，选择是继续调用流程还是直接结束会话，不要重复发送消息,完成后调用 end_reply"


class PrefrontalCortex:
    """AI 工作记忆中枢：短期记忆、任务管理、工具召回与态势感知。

    职责：
    - 消息队列管理（pending_user / pending_group / _general_tasks）
    - 工具系统提示构建（频道能力 + 工具目录 + 媒体处理规则）
    - 基于命中计数的工具召回（top-N 热工具常驻）
    - 标签驱动的工具自动注入（media:TYPE -> 工具匹配）
    - LLM 上下文组装（人设 + 工作记忆 + 对话历史 + 语义记忆）
    """

    def __init__(
            self,
            everything_data: EverythingData,
            channel_manager: Optional["ChannelManager"] = None,
            conversation_data: Optional["ConversationData"] = None,
    ) -> None:
        self.temporary: list[Dict] = []
        self.record: dict[str, int] = {}
        self.everything_data = everything_data
        self._channel_manager = channel_manager
        self._conversation_data = conversation_data

        # 消息任务队列
        self.pending_user: UniqueQueue[Union[int, str]] = UniqueQueue()
        self.pending_group: UniqueQueue[Union[int, str]] = UniqueQueue()
        self.pending_analysis: UniqueQueue[tuple[Union[int, str], Union[int, str]]] = UniqueQueue()
        self._pending_images: List[ImageContent] = []
        self._pending_media: list = []

        # scope → 消息预览 / adapter_key 路由
        self._message_previews: dict[str, str] = {}
        self._task_adapter_keys: dict[str, str] = {}
        # 群聊 scope → 最近发送者 [(uid, name), ...]
        self._group_recent_senders: dict[str, list[tuple[str, str]]] = {}

        # 通用任务（错误反馈、AI 自主任务、画像分析等）
        self._general_tasks: list[MindTask] = []

        # 工具召回：tool_name → 累计命中次数
        self._tool_recall: dict[str, int] = {}
        # 因标签匹配而激活的工具名（整个思维会话有效，会话结束后清理）
        self._tag_activated_tools: set[str] = set()
        # 通过 list_entity_methods 动态发现的工具名（整个思维会话有效，会话结束后清理）
        self._discovered_tools: set[str] = set()

    @property
    def _max_temp(self) -> int:
        return _get_mind_config().short_term_memory_size

    @property
    def _tool_recall_top_n(self) -> int:
        return _get_mind_config().tool_recall_top_n

    # ==================================================================
    # 消息入队
    # ==================================================================

    async def add_task(self, anything: Everything) -> None:
        """将消息加入待处理队列，收集附带媒体，触发画像分析检查。"""
        if anything.images:
            self._pending_images.extend(anything.images)
        if hasattr(anything, "media_segments") and anything.media_segments:
            self._pending_media.extend(anything.media_segments)

        preview = anything.get_text_content()[:300] if hasattr(anything, "get_text_content") else str(anything)[:300]
        adapter_key = getattr(anything, "adapter_key", "") or ""

        scope = anything.entity_scope
        if isinstance(anything, EverythingGroup) and anything.is_group_scope:
            self.pending_group.append(anything.group_id)
            self._message_previews[scope] = preview
            if adapter_key:
                self._task_adapter_keys[scope] = adapter_key
            uid = str(anything.uid) if anything.uid and anything.uid not in (0, "0") else ""
            name = getattr(anything, "user_name", "") or getattr(anything, "nickname", "") or ""
            if uid:
                senders = self._group_recent_senders.setdefault(scope, [])
                entry = (uid, name)
                if entry not in senders:
                    senders.append(entry)
                if len(senders) > 10:
                    senders[:] = senders[-10:]
            await self._handle_group_message(anything)
        else:
            self.pending_user.append(anything.uid)
            self._message_previews[scope] = preview
            if adapter_key:
                self._task_adapter_keys[scope] = adapter_key

        await self._handle_user_message(anything)
        self._scan_message_tags(str(anything))

    def _scan_message_tags(self, content: str) -> None:
        """扫描消息中的标签，按 key 和 value 搜索匹配工具。

        [media_type:image][media_path:path] -> tag "media:image"
        [media_file:image:path]             -> tag "media:image"
        [channel:telegram]                  -> tag "channel", "telegram"
        [platform:qq]                       -> tag "platform", "qq"
        """
        tags = etag_all(content)
        for key, value in tags:
            if key in ("media_type", "media_file"):
                # [media_type:image] 的 value 即媒体类型；[media_file:image:path] 取首段
                media_kind = value.split(":", 1)[0] if value else ""
                if media_kind:
                    self._activate_by_tag(f"media:{media_kind}")
            else:
                self._activate_by_tag(key)
                first_val = value.split(":")[0] if value else ""
                if first_val and first_val != key:
                    self._activate_by_tag(first_val)

    def _activate_by_tag(self, tag_query: str) -> None:
        """按 tag 查询 EntityRegistry，将匹配的工具加入激活集。"""
        matched = EntityRegistry.get_by_tag(tag_query)
        for entity in matched:
            if entity.enabled and entity.func is not None:
                if entity.name not in self._tag_activated_tools:
                    self._tag_activated_tools.add(entity.name)
                    log(f"标签激活工具: [{tag_query}] -> {entity.name}", "DEBUG", tag="PFC")

    def activate_media_tools(self, images: list, media_segments: list) -> None:
        """按消息实际携带的媒体激活对应媒体工具（recognize_image / voice_to_text 等）。

        图片/媒体段是结构化字段而非文本标签（[media_type:*] 标签在入库时才生成），
        文本标签扫描覆盖不到，需按媒体对象显式激活。
        """
        if images:
            self._activate_by_tag("media:image")
        for seg in media_segments or []:
            seg_type = getattr(seg, "type", None)
            type_name = seg_type.value if hasattr(seg_type, "value") else str(seg_type or "")
            if type_name:
                self._activate_by_tag(f"media:{type_name}")

    @staticmethod
    def _analysis_threshold() -> int:
        try:
            from agent.config import get_config_provider
            return get_config_provider().mind.conversation_analysis_threshold
        except Exception:
            return 5

    async def _handle_group_message(self, anything: EverythingGroup) -> None:
        """群组消息达到阈值时加入画像分析队列，分析后重置计数实现周期性增量更新。"""
        group_entity = await self.everything_data.get_anything(anything.group_id, 0)
        conv_count = group_entity.add_conversations_num()
        threshold = self._analysis_threshold()
        if conv_count >= threshold:
            has_personality = bool(group_entity.personality.get("personality"))
            if conv_count > threshold or not has_personality:
                self.pending_analysis.append((group_entity.group_id, group_entity.uid or 0))
                group_entity.reset_conversations_num()

    async def _handle_user_message(self, anything: Everything) -> None:
        """用户消息达到阈值时加入画像分析队列；首次出现的用户自动建档。

        达到阈值后重置计数器，实现周期性增量画像更新。
        """
        group_id = anything.group_id if isinstance(anything, EverythingGroup) else 0
        user_entity = await self.everything_data.get_anything(group_id, anything.uid)
        conv_count = user_entity.add_conversations_num()
        has_personality = bool(user_entity.personality.get("personality"))

        if conv_count == 1 and not has_personality:
            uid_str = str(anything.uid)
            self._general_tasks.append(MindTask(
                task_type=TaskType.PROFILE,
                scope=f"user_{uid_str}",
                uid=anything.uid,
                preview=f"新用户 {uid_str} 首次出现，建立画像",
            ))
            self.pending_analysis.append((group_id, user_entity.uid or 0))
        elif conv_count >= self._analysis_threshold():
            self.pending_analysis.append((group_id, user_entity.uid or 0))
            user_entity.reset_conversations_num()

    # ==================================================================
    # 消息任务消费
    # ==================================================================

    async def pop_user_task(self) -> Optional[Union[int, str]]:
        if not self.pending_user.is_empty():
            uid = self.pending_user.popleft()
            scope = f"user_{uid}"
            self._message_previews.pop(scope, None)
            self._task_adapter_keys.pop(scope, None)
            return uid
        return None

    async def pop_group_task(self) -> Optional[Union[int, str]]:
        if not self.pending_group.is_empty():
            gid = self.pending_group.popleft()
            scope = f"group_{gid}"
            self._message_previews.pop(scope, None)
            self._task_adapter_keys.pop(scope, None)
            return gid
        return None

    async def pop_analysis_task(self) -> Optional[EntityData]:
        if not self.pending_analysis.is_empty():
            group_id, uid = self.pending_analysis.popleft()
            return await self.everything_data.get_anything(group_id, uid)
        return None

    # ==================================================================
    # 通用任务队列
    # ==================================================================

    def add_general_task(self, task: MindTask) -> None:
        self._general_tasks.append(task)

    def peek_general_tasks(self) -> list[MindTask]:
        return list(self._general_tasks)

    def consume_general_task(self, index: int) -> bool:
        if 0 <= index < len(self._general_tasks):
            self._general_tasks.pop(index)
            return True
        return False

    def clear_general_tasks(self) -> int:
        count = len(self._general_tasks)
        self._general_tasks.clear()
        return count

    # ==================================================================
    # 态势感知
    # ==================================================================

    def peek_all_tasks(self) -> List[Tuple[str, Union[int, str], Union[int, str], str]]:
        """查看所有待处理消息任务（不消费）。"""
        result: List[Tuple[str, Union[int, str], Union[int, str], str]] = []
        for uid in self.pending_user.queue:
            scope = f"user_{uid}"
            preview = self._message_previews.get(scope, "")
            result.append((scope, uid, 0, preview))
        for gid in self.pending_group.queue:
            scope = f"group_{gid}"
            preview = self._message_previews.get(scope, "")
            result.append((scope, 0, gid, preview))
        return result

    def consume_user_task(self, uid: Union[int, str]) -> bool:
        scope = f"user_{uid}"
        self._message_previews.pop(scope, None)
        self._task_adapter_keys.pop(scope, None)
        return self._consume_from_queue(self.pending_user, uid)

    def consume_group_task(self, group_id: Union[int, str]) -> bool:
        scope = f"group_{group_id}"
        self._message_previews.pop(scope, None)
        self._task_adapter_keys.pop(scope, None)
        self._group_recent_senders.pop(scope, None)
        return self._consume_from_queue(self.pending_group, group_id)

    @staticmethod
    def _consume_from_queue(queue: UniqueQueue, key: Union[int, str]) -> bool:
        """从去重队列中消费元素，兼容 int/str 类型差异。"""
        candidates = {key, str(key)}
        if isinstance(key, str) and key.lstrip("-").isdigit():
            candidates.add(int(key))
        for candidate in candidates:
            if candidate in queue.seen:
                queue.seen.discard(candidate)
                try:
                    queue.queue.remove(candidate)
                except ValueError:
                    pass
                return True
        return False

    def has_pending_tasks(self) -> bool:
        return (
                not self.pending_user.is_empty()
                or not self.pending_group.is_empty()
                or bool(self._general_tasks)
        )

    def set_adapter_key(self, scope: str, adapter_key: str) -> None:
        """注册 scope → adapter_key 映射（支撑主动消息路由）。"""
        if scope and adapter_key:
            self._task_adapter_keys[scope] = adapter_key

    def get_adapter_key(self, scope: str) -> str:
        return self._task_adapter_keys.get(scope, "")

    # ==================================================================
    # 工具管理：召回 / 频道 / 标签 / 活跃集
    # ==================================================================

    def record_tool_use(self, tool_name: str) -> None:
        """记录工具使用，命中计数 +1。"""
        prev = self._tool_recall.get(tool_name, 0)
        self._tool_recall[tool_name] = prev + 1
        log(f"工具命中: {tool_name} ({prev} -> {prev + 1})", "DEBUG", tag="PFC")

    def get_tool_use_total(self) -> int:
        """返回累计工具命中总次数。"""
        return sum(self._tool_recall.values())

    def get_hot_tool_names(self) -> list[str]:
        """返回 top-N 热工具名（按命中次数降序）。"""
        if not self._tool_recall:
            return []
        sorted_tools = sorted(self._tool_recall.items(), key=lambda x: x[1], reverse=True)
        hot = [name for name, _ in sorted_tools[:self._tool_recall_top_n]]
        if hot:
            recall_detail = ", ".join(f"{n}({self._tool_recall[n]})" for n in hot)
            log(f"热工具 top-{self._tool_recall_top_n}: [{recall_detail}]", "DEBUG", tag="PFC")
        return hot

    def get_hot_tool_schemas(self) -> list[dict]:
        """返回 top-N 热工具的 schema。"""
        names = self.get_hot_tool_names()
        if not names:
            return []
        return EntityRegistry.get_tool_schema_by_names(names)

    def get_channel_tool_schemas(self, adapter_key: str) -> list[dict]:
        """根据频道能力集，按 capability 值作为 tag 搜索全局工具。

        每个 ChannelCapability 的 value（如 "send_text"、"edit_message"）
        会作为 tag 在 EntityRegistry 中搜索，匹配到的工具全部加入。
        被该频道按频道禁用的公共能力工具在此过滤（专属工具由实体
        enabled 状态过滤）。
        """
        if not adapter_key or not self._channel_manager:
            return []
        channel = self._channel_manager.get(adapter_key)
        if not channel:
            return []
        from agent.channel.tool_bridge import is_channel_tool_enabled

        cap_tags = [c.value for c in channel.capabilities]
        schemas = [
            s for s in EntityRegistry.get_tool_schema_by_tags(cap_tags)
            if is_channel_tool_enabled(adapter_key, s.get("function", {}).get("name", ""))
        ]
        if schemas:
            names = [s.get("function", {}).get("name", "") for s in schemas]
            log(f"频道工具 [{adapter_key}] ({len(cap_tags)} 能力): {', '.join(names)}", "DEBUG", tag="PFC")
        return schemas

    def resolve_tag_tool_schemas(self) -> list[dict]:
        """返回当前因标签匹配而激活的工具 schema。"""
        if not self._tag_activated_tools:
            return []
        return EntityRegistry.get_tool_schema_by_names(list(self._tag_activated_tools))

    def expand_discovered_tools(self, tool_calls: list) -> None:
        """解析 list_entity_methods 调用结果，将发现的工具加入动态发现集。"""
        import json as _json
        for tc in tool_calls:
            if tc.name != "list_entity_methods":
                continue
            try:
                args = _json.loads(tc.arguments) if isinstance(tc.arguments, str) else (tc.arguments or {})
                group = args.get("group", "")
                if not group:
                    continue
                for schema in EntityRegistry.get_tool_schemas_by_group(group):
                    name = schema["function"]["name"]
                    if name not in self._discovered_tools:
                        self._discovered_tools.add(name)
                        log(f"动态发现工具: {name} (来自分组 {group})", "DEBUG", tag="PFC")
            except Exception as e:
                log(f"动态工具发现失败: {e}", "DEBUG", tag="PFC")

    def clear_dynamic_tools(self) -> None:
        """清除当轮动态工具状态（tag 激活 + 动态发现）。"""
        self._tag_activated_tools.clear()
        self._discovered_tools.clear()

    async def get_active_tool_schemas(self, adapter_key: str = "", scope: str = "") -> list[dict]:
        """合并返回当前所有活跃工具 schema（always + 频道 + 标签 + 热召回 + 动态发现 + 已激活分组）。

        合并结果经两道门控过滤：
        1. 沉睡过滤：allow_sleep 工具所属分组未激活时不出现在 schema 中；
           已激活分组的全部工具补充进来
        2. check_fn 门控：前置条件不满足的工具被过滤（core.tool_gate）
        """
        from agent.mind.tool_activation import tool_activation

        seen_names: set[str] = set()
        all_schemas: list[dict] = []
        source_counts: dict[str, int] = {}

        def _merge(schemas: list[dict], source: str) -> None:
            added = 0
            for s in schemas:
                name = s.get("function", {}).get("name", "")
                if name and name not in seen_names:
                    seen_names.add(name)
                    all_schemas.append(s)
                    added += 1
            if added:
                source_counts[source] = added

        _merge(EntityRegistry.get_tool_schema_by_tags(["always"]), "always")

        if adapter_key:
            _merge(self.get_channel_tool_schemas(adapter_key), f"channel:{adapter_key}")
            _merge(EntityRegistry.get_tool_schema_by_tags([adapter_key]),
                   f"channel_tag:{adapter_key}")

        _merge(self.resolve_tag_tool_schemas(), "tag_match")
        _merge(self.get_hot_tool_schemas(), "hot_recall")

        if self._discovered_tools:
            _merge(EntityRegistry.get_tool_schema_by_names(
                list(self._discovered_tools)), "discovered")

        # 已激活的沉睡分组：补充其全部工具（即使未被上述渠道命中）
        activated = tool_activation.active_groups(scope)
        for group in activated:
            _merge(EntityRegistry.get_tool_schemas_by_group(group), f"activated:{group}")

        # 沉睡过滤：移除未激活分组中的可沉睡工具
        sleepable_groups = EntityRegistry.get_sleepable_groups()
        if sleepable_groups:
            before = len(all_schemas)
            all_schemas = [
                s for s in all_schemas
                if not self._is_sleeping_tool(
                    s.get("function", {}).get("name", ""), sleepable_groups, scope,
                )
            ]
            slept = before - len(all_schemas)
            if slept:
                source_counts["sleeping"] = -slept

        # check_fn 门控过滤
        names = [s.get("function", {}).get("name", "") for s in all_schemas]
        active_entities = await EntityRegistry.get_active_tools(names)
        active_names = {e.name for e in active_entities}
        all_schemas = [
            s for s in all_schemas
            if s.get("function", {}).get("name", "") in active_names
        ]

        sources = ", ".join(f"{k}={v}" for k, v in source_counts.items())
        tool_names = [s.get("function", {}).get("name", "") for s in all_schemas]
        log(f"活跃工具集: {len(all_schemas)} 个 ({sources}) [{', '.join(tool_names)}]", "DEBUG", tag="PFC")

        return all_schemas

    @staticmethod
    def _is_sleeping_tool(tool_name: str, sleepable_groups: dict, scope: str) -> bool:
        """判断工具当前是否处于沉睡状态（可沉睡且所属分组未激活）。"""
        from agent.mind.tool_activation import tool_activation
        entity = EntityRegistry.get(tool_name)
        if entity is None or not (entity.allow_sleep and entity.sleep_brief):
            return False
        return not tool_activation.is_active(entity.group, scope)

    # ==================================================================
    # 系统提示构建
    # ==================================================================

    def build_tool_system_prompt(
            self,
            models_summary: str = "",
            adapter_key: str = "",
            target_id: str = "",
            direct_vision: bool = False,
    ) -> list[dict]:
        """构建工具使用规则、通道感知、媒体处理规则的系统提示。"""
        catalog = EntityRegistry.get_entity_catalog()
        if not catalog:
            return []

        mc = _get_mind_config()
        rules = mc.tool_system_rules if hasattr(mc, "tool_system_rules") else []
        lines = list(rules) + ["# 工具分组目录"]

        # 可沉睡分组：未激活时仅展示 brief，提示 AI 按需激活（节省 token）
        from agent.mind.tool_activation import tool_activation
        sleepable_groups = EntityRegistry.get_sleepable_groups()

        for entry in catalog:
            group = entry["group"]
            desc = entry.get("description", "")
            desc_part = f" — {desc}" if desc else ""
            sleep_info = sleepable_groups.get(group)
            if sleep_info and not tool_activation.is_active(group):
                lines.append(
                    f"- {group} ({entry['tool_count']}){desc_part} "
                    f"[沉睡] {sleep_info['brief']}"
                    f"（需要时调用 activate_tool_group(group=\"{group}\") 激活）"
                )
            else:
                lines.append(f"- {group} ({entry['tool_count']}){desc_part}")

        if models_summary:
            lines.append("")
            lines.append(models_summary)

        media_rules = self._build_media_rules(direct_vision)
        if media_rules:
            lines.append("")
            lines.append("# 多媒体处理")
            lines.append(media_rules)

        context_reading_rules = self._build_context_reading_rules()
        if context_reading_rules:
            lines.append("")
            lines.append(context_reading_rules)

        # 工具使用指引 + 记忆使用提示（静态引导，归入 stable 层冻结复用）
        lines.append("")
        lines.append(_TOOL_USAGE_RULES)
        lines.append("")
        lines.append(_MEMORY_USAGE_HINT)

        lines.append("")
        lines.append(_PARALLEL_CALL_HINT)

        # 后台任务行为规范：仅子代理委托启用时注入（无后台任务来源则规则无意义）
        if _delegation_enabled():
            lines.append("")
            lines.append(_BACKGROUND_TASK_HINT)

        return [{"role": "system", "content": "\n".join(lines)}]

    @staticmethod
    def _build_context_reading_rules() -> str:
        """构建上下文解读和人物关系理解规则。"""
        return """# 对话上下文理解

## 消息标签
对话中的 [key:value] 标签含义：
- [uid:xxx] — 消息发送者的用户ID，同一uid是同一人
- [name:xxx] — 发送者用户名
- [nickname:xxx] — 发送者群内昵称
- [channel:xxx] — 消息来源频道标识（adapter_key），send_message 等频道工具的 channel_id 参数应填此值
- [session_id:xxx] — 会话ID（同一频道内会话上下文标识）
- [group_id:xxx] — 群组ID，不同group_id是不同群
- [message_id:xxx] — 当前消息ID，可用于精确定位某一条消息
- [at_uid:xxx] — 消息中 @ 提及的用户ID
- [at_uid:all] — @ 全体成员
- [reply_to:xxx] — 引用的原消息

## 人物识别
- 以 uid 为准识别身份，name/nickname 可能变化
- 群聊中 [uid:xxx] 是这条消息的发送者
- [at_uid:xxx] 是消息中被 @ 的人的 uid
- 当 [at_uid:xxx] 中的 xxx 是你自己的 uid 时，表示有人在 @ 你，需要回应

## @ 提及用户
在 send_message 的 content 中使用 [at_uid:xxx] 可以 @ 提及用户：
- [at_uid:12345] — @ uid 为 12345 的用户
- [at_uid:all] — @ 全体成员
- 示例：看到 [uid:12345] 的消息，回复时写 [at_uid:12345] 即可 @ 该用户
- 不需要 @ 时直接写普通文本"""

    @staticmethod
    def _build_media_rules(direct_vision: bool = False) -> str:
        """根据 EntityRegistry 中的 media:TYPE 标签动态生成媒体处理规则。

        Args:
            direct_vision: 当前主模型支持视觉时，图片直接以多模态形式呈现，
                无需强制调用图片识别工具（仍保留工具供深入分析）。
        """
        tag_tool_map: dict[str, list[str]] = {}
        for entity in EntityRegistry.get_all():
            if entity.entity_type.value != "tool" or not entity.enabled:
                continue
            for tag in entity.tags:
                if tag.startswith("media:"):
                    media_type = tag[6:]
                    tag_tool_map.setdefault(media_type, []).append(entity.name)

        if not tag_tool_map:
            return ""

        lines = [
            "对话中出现 [media_type:类型][media_path:路径] 标签时，**必须优先使用下列内置媒体工具**处理：",
        ]
        for media_type, tool_names in sorted(tag_tool_map.items()):
            tools_str = " / ".join(tool_names)
            if media_type == "image" and direct_vision:
                lines.append(
                    f"- [media_type:image] → 图片已直接以视觉形式呈现给你，无需调用工具识别；"
                    f"如需更深入分析（OCR/细节）仍可调用 {tools_str}"
                )
            else:
                lines.append(f"- [media_type:{media_type}] → {tools_str}")
        lines.append(
            "禁止用 run_shell_command 编写脚本（如 python HTTP 请求）替代上述媒体工具——"
            "内置工具已封装好多模型回退，更可靠。"
        )
        lines.append("媒体分析是耗时操作，应与其他独立操作并行发起，避免阻塞对话。")
        return "\n".join(lines)

    # ==================================================================
    # LLM 上下文组装（Prompt 分层缓存架构）
    # ==================================================================

    def build_stable_layer(
            self,
            persona_parts: List[str],
            models_summary: str = "",
            direct_vision: bool = False,
    ) -> str:
        """构建 stable 层：人设 + 工具系统提示（对话内字节级不变，供前缀缓存复用）。"""
        parts = list(persona_parts)
        for msg in self.build_tool_system_prompt(
                models_summary=models_summary, direct_vision=direct_vision,
        ):
            if msg.get("content"):
                parts.append(msg["content"])
        return "\n\n".join(parts)

    def stable_fingerprint(self, models_summary: str = "", direct_vision: bool = False) -> str:
        """计算 stable 层动态输入的指纹（任一输入变化即触发重建）。

        覆盖：工具目录、可沉睡分组及其激活状态、工具规则、模型摘要、媒体规则。
        """
        import json as _json

        from agent.mind.prompt_layers import prompt_cache_manager
        from agent.mind.tool_activation import tool_activation

        mc = _get_mind_config()
        rules = mc.tool_system_rules if hasattr(mc, "tool_system_rules") else []
        catalog = EntityRegistry.get_entity_catalog()
        sleepable = EntityRegistry.get_sleepable_groups()
        activated = tool_activation.active_groups()
        return prompt_cache_manager.compute_hash(
            _json.dumps(catalog, sort_keys=True, ensure_ascii=False),
            _json.dumps(sleepable, sort_keys=True, ensure_ascii=False),
            _json.dumps(sorted(activated.items()), ensure_ascii=False),
            "\n".join(rules),
            models_summary,
            self._build_media_rules(direct_vision),
            str(_delegation_enabled()),
        )

    async def build_llm_context(
            self,
            *,
            stable_text: str = "",
            context_text: str = "",
            memory_msgs: List[Dict],
            anything: Optional["Everything"] = None,
            adapter_key: str = "",
            target_id: str = "",
            models_summary: str = "",
            anthropic_breakpoint: bool = False,
    ) -> List[Dict]:
        """组装完整 LLM 上下文（分层架构），每次调用实时从 DB 获取最新对话历史。

        消息顺序（stable/context 层在前且字节稳定，供 Prompt Caching 前缀复用）：
        1. stable 层（人设 + 工具提示，对话内冻结）
        2. context 层（便签等低频内容）
        3. volatile 层（短期记忆 + 溢出提示 + 安全标记 + 语义召回）
        4. 对话历史（最近 max_conversation_size 条）
        """
        system_msgs: List[Dict] = []
        if stable_text:
            stable_msg: Dict = {"role": "system", "content": stable_text}
            if anthropic_breakpoint:
                # Anthropic Prompt Caching 断点：stable 层标记为可缓存前缀
                stable_msg["cache_control"] = {"type": "ephemeral"}
            system_msgs.append(stable_msg)
        if context_text:
            system_msgs.append({"role": "system", "content": context_text})

        # volatile 层：短期记忆片段（角色按存储原样使用，主流格式不做转换）
        volatile_msgs: List[Dict] = list(self.temporary)

        # 实时从 DB 获取最新对话历史（必须每轮重新获取，不可缓存或外部传入！
        # 多轮 think_loop 期间用户可能发送新消息，必须确保每轮都能拿到最新对话）
        conversation_list: List[Dict] = []
        max_size = 0
        if self._conversation_data and anything:
            max_size = self._conversation_data.max_size
            conversation_list = await self._conversation_data.get_conversation_record_by_everything(anything)
            log(f"对话历史: {len(conversation_list)} 条 (窗口上限 {max_size})", "DEBUG", tag="PFC")

        # 会话令牌：为历史消息包裹可信标记（防 prompt 注入伪造历史）
        security_hint: List[Dict] = []
        try:
            from agent.security.session_token import (
                build_token_rule_hint, current_token, wrap_history_content,
            )
            if current_token():
                conversation_list = [
                    {**m, "content": wrap_history_content(m["content"])}
                    if isinstance(m.get("content"), str) else m
                    for m in conversation_list
                ]
                hint = build_token_rule_hint()
                if hint:
                    security_hint = [{"role": "system", "content": hint}]
        except Exception:
            pass

        # 上下文溢出提示：对话历史达到窗口上限时，告知窗口外真实数量与检索路径
        # （窗口外消息仍完整存于 DB——软归档感知，而非沉默丢弃）
        overflow_hint: List[Dict] = []
        if max_size > 0 and len(conversation_list) >= max_size:
            hidden = 0
            try:
                total = await self._conversation_data.count_messages(anything)
                hidden = max(0, total - len(conversation_list))
            except Exception as exc:
                log(f"窗口外消息计数失败: {exc}", "DEBUG", tag="PFC")
            hidden_note = f"，另有 {hidden} 条更早消息在窗口外" if hidden else ""
            overflow_hint = [{"role": "system", "content": (
                f"[上下文溢出] 当前仅显示最近 {max_size} 条对话{hidden_note}，更早的消息已不在视野内。\n"
                "- 可通过 recall_conversation 按语义搜索窗口外的对话内容\n"
                "- 建议使用 memorize 将对话中的重要信息存入长期记忆，避免遗忘\n"
                "- 可通过 recall 检索长期记忆中的相关信息"
            )}]

        all_msgs = (
            system_msgs + volatile_msgs + overflow_hint + security_hint
            + memory_msgs + conversation_list
        )

        # 确保最后一条非 system 消息不是 assistant 角色，防止 Anthropic prefill 400 错误。
        # （规则实现已收拢至 message_schema.fix_trailing_assistant，此处就地委托）
        from agent.mind.message_schema import fix_trailing_assistant
        fix_trailing_assistant(all_msgs)

        return all_msgs

    def _build_scene_info(
        self,
        anything: Optional["Everything"],
        adapter_key: str = "",
    ) -> str:
        """构建当前对话场景信息（私聊/群聊、群组ID、频道、发送者等）。"""
        if not anything:
            return ""

        parts: list[str] = []

        group_id = getattr(anything, "group_id", None)
        uid = getattr(anything, "uid", None)
        channel_key = adapter_key or getattr(anything, "adapter_key", "")

        if group_id and group_id not in (0, "0", ""):
            parts.append(f"群聊 group_id={group_id}")
            scope = f"group_{group_id}"
            senders = self._group_recent_senders.get(scope, [])
            if senders:
                desc = ", ".join(f"uid:{s[0]}({s[1]})" for s in senders if s[0])
                if desc:
                    parts.append(f"待回复消息来自: {desc}")
        elif uid and uid not in (0, "0", ""):
            parts.append(f"私聊 uid={uid}")

        if channel_key:
            parts.append(f"频道={channel_key}")

        if not parts:
            return ""

        return f"[当前场景] {' | '.join(parts)}"

    # ==================================================================
    # 短期记忆
    # ==================================================================

    def add_temporary(self, temporary_clip: Dict) -> None:
        self.temporary.append(temporary_clip)
        if len(self.temporary) > self._max_temp:
            self.temporary = self.temporary[-self._max_temp:]

    def build_execution_context(
            self,
            execution_steps: list[str],
            start_time: float,
            iteration: int,
            *,
            adapter_key: str = "",
            safety_limit: int = 0,
            anything: Optional["Everything"] = None,
    ) -> dict:
        """构建当前轮次的执行状态消息（轮次、耗时、工具态势、频道、历史步骤、待处理消息）。"""
        import time
        elapsed = time.time() - start_time
        remaining = (safety_limit - iteration) if safety_limit > 0 else None

        lines: list[str] = []

        # 当前对话场景信息
        scene_info = self._build_scene_info(anything, adapter_key)
        if scene_info:
            lines.append(scene_info)

        if iteration == 0:
            limit_hint = f"最多 {safety_limit} 轮" if safety_limit > 0 else ""
            lines.append(f"[系统提示] 新一轮对话开始 | 请仔细分析上下文后决定操作{' | ' + limit_hint if limit_hint else ''}")
        else:
            round_info = f"第 {iteration + 1} 轮"
            if remaining is not None:
                round_info += f" | 剩余 {remaining} 轮"
            round_info += f" | 已耗时 {elapsed:.2f}秒"
            lines.append(f"[系统提示] {round_info}")

            # 超时风险预警：耗时超过 llm_timeout 的 60% 时提醒可切换模型
            try:
                llm_timeout = _get_mind_config().llm_timeout
            except Exception:
                llm_timeout = 0
            if llm_timeout > 0 and elapsed > llm_timeout * 0.6:
                lines.append(
                    f"[超时预警] 本轮已耗时 {elapsed:.0f}s（配置上限 {llm_timeout:.0f}s），"
                    "若当前模型响应慢，可调用 switch_model 切换到更快的模型后继续。"
                )

            # 剩余轮次警告（动态强度）
            if remaining is not None:
                if remaining == 1:
                    lines.append(_FINAL_ROUND_WARNING)
                elif remaining == 2:
                    lines.append(_URGENT_ROUND_WARNING)
                elif remaining <= safety_limit // 2:
                    lines.append(
                        f"[轮次提醒] 已用 {iteration + 1}/{safety_limit} 轮，"
                        "建议优先完成核心操作，不必要的步骤可跳过。"
                    )

        # 工具态势摘要
        tool_parts: list[str] = []
        if self._tag_activated_tools:
            tool_parts.append(f"标签激活: {', '.join(sorted(self._tag_activated_tools))}")
        if self._discovered_tools:
            tool_parts.append(f"动态发现: {', '.join(sorted(self._discovered_tools))}")
        hot = self.get_hot_tool_names()[:5]
        if hot:
            tool_parts.append(f"热工具: {', '.join(hot)}")
        if tool_parts:
            lines.append(f"[工具态势] {' | '.join(tool_parts)}")

        # 目标 nag 提醒（对齐 Claude Code todo_reminder：10 轮未更新才提醒）
        try:
            from agent.planning.nag import maybe_nag
            from agent.mind.tool_activation import ToolActivationManager
            nag_text = maybe_nag(ToolActivationManager.current_scope())
            if nag_text:
                lines.append(nag_text)
        except Exception:
            pass

        # 沉睡分组激活状态（剩余最后一轮时提示续期）
        from agent.mind.tool_activation import tool_activation
        active_groups = tool_activation.active_groups()
        if active_groups:
            group_desc = ", ".join(f"{g}(剩余{r}轮)" for g, r in sorted(active_groups.items()))
            lines.append(f"[已激活工具分组] {group_desc}")
            expiring = [g for g, r in active_groups.items() if r <= 1]
            if expiring:
                lines.append(
                    f"⚠️ 分组 {', '.join(expiring)} 即将回到沉睡，"
                    "如下轮仍需使用请立即调用 activate_tool_group 续期。"
                )

        # 频道信息
        if adapter_key and self._channel_manager:
            channel = self._channel_manager.get(adapter_key)
            if channel:
                info = channel.get_status_info()
                cap_count = len(info.get("capabilities", []))
                lines.append(f"[当前频道] {adapter_key} ({info.get('name', '?')}) | {cap_count} 项能力")

        # 短期记忆状态
        if self.temporary:
            lines.append(f"[短期记忆] {len(self.temporary)}/{self._max_temp} 条")

        if execution_steps:
            lines.append("[已完成步骤（以下操作已执行成功，请勿重复）]")
            lines.extend(execution_steps)

        pending = self.peek_all_tasks()
        if pending:
            lines.append(f"[待处理消息] {len(pending)} 条：")
            for scope, uid, group_id, preview in pending[:3]:
                lines.append(f"  • {scope}: {preview}")
            if len(pending) > 3:
                lines.append(f"  • ...还有 {len(pending) - 3} 条")
            lines.append(_PENDING_HINT)
        else:
            lines.append(_NO_PENDING_HINT)

        return {"role": "system", "content": "\n".join(lines)}

    def collect_images(self) -> List[ImageContent]:
        images = self._pending_images
        self._pending_images = []
        return images

    def collect_media(self) -> list:
        """收集并清空待处理的媒体片段。"""
        media = self._pending_media
        self._pending_media = []
        return media

    # ==================================================================
    # 管理与监控接口
    # ==================================================================

    def delete_temporary(self, index: int) -> bool:
        if 0 <= index < len(self.temporary):
            self.temporary.pop(index)
            return True
        return False

    def clear_temporary(self) -> int:
        count = len(self.temporary)
        self.temporary.clear()
        return count

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
        for scope, uid, group_id, preview in self.peek_all_tasks():
            adapter_key = self._task_adapter_keys.get(scope, "")
            pending_msgs.append({
                "scope": scope, "uid": uid, "group_id": group_id,
                "preview": preview, "adapter_key": adapter_key,
            })

        general_tasks = []
        for t in self.peek_general_tasks():
            general_tasks.append({
                "type": t.task_type.value, "scope": t.scope, "preview": t.preview,
            })

        tool_recall_sorted = sorted(
            self._tool_recall.items(), key=lambda x: x[1], reverse=True,
        )

        try:
            from agent.mind.prompt_layers import prompt_cache_manager
            cache_stats = prompt_cache_manager.stats()
        except Exception:
            cache_stats = {}

        return {
            "tool_recall": [{"name": n, "count": c} for n, c in tool_recall_sorted],
            "tool_recall_top_n": self._tool_recall_top_n,
            "tag_activated_tools": sorted(self._tag_activated_tools),
            "discovered_tools": sorted(self._discovered_tools),
            "pending_messages": pending_msgs,
            "general_tasks": general_tasks,
            "pending_analysis_count": len(self.pending_analysis),
            "short_term_memory_count": len(self.temporary),
            "short_term_memory_max": self._max_temp,
            "prompt_cache": cache_stats,
        }
