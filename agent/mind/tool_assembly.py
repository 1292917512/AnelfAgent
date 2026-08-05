"""ToolAssembly — 工具装配：召回、tag 激活、动态发现、schema 合并与门控。

从 PrefrontalCortex 拆分而来。本模块为自包含状态类，不依赖 Mind/PFC，
EntityRegistry 与 tool_gate 是唯一外部依赖，便于独立测试（与 guardrails.py 同模式）。

职责：
- 基于命中计数的工具召回（top-N 热工具常驻）
- 标签驱动的工具自动注入（media:TYPE → 工具匹配）
- list_entity_methods 动态发现
- 活跃工具集合并（always + 频道 + 标签 + 热召回 + 动态发现 + 已激活分组）
  经沉睡过滤与 check_fn 门控后按相关性排序
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from core.entity import EntityRegistry
from core.log import log

if TYPE_CHECKING:
    from agent.channel.manager import ChannelManager


class ToolAssembly:
    """工具召回与活跃工具集装配（PFC 工具面组件）。"""

    def __init__(self, channel_manager: Optional["ChannelManager"] = None) -> None:
        self._channel_manager = channel_manager
        # 工具召回：tool_name → 累计命中次数
        self._tool_recall: dict[str, int] = {}
        # 因标签匹配而激活的工具名（整个思维会话有效，会话结束后清理）
        self._tag_activated_tools: set[str] = set()
        # 通过 list_entity_methods 动态发现的工具名（整个思维会话有效，会话结束后清理）
        self._discovered_tools: set[str] = set()
        # 动态工具集版本号（tag 激活/动态发现变化时递增，供 think_loop 检测重建）
        self._tools_version: int = 0

    @property
    def tools_version(self) -> int:
        """动态工具集版本号（tag 激活/动态发现变化时递增）。"""
        return self._tools_version

    @property
    def _tool_recall_top_n(self) -> int:
        from agent.config import get_mind_config
        return get_mind_config().tool_recall_top_n

    @property
    def tool_recall_top_n(self) -> int:
        """热工具召回 top-N 配置（状态快照等外部读取用）。"""
        return self._tool_recall_top_n

    # ==================================================================
    # tag 激活 / 媒体工具
    # ==================================================================

    def activate_by_tag(self, tag_query: str) -> None:
        """按 tag 查询 EntityRegistry，将匹配的工具加入激活集。"""
        matched = EntityRegistry.get_by_tag(tag_query)
        for entity in matched:
            if entity.enabled and entity.func is not None:
                if entity.name not in self._tag_activated_tools:
                    self._tag_activated_tools.add(entity.name)
                    self._tools_version += 1
                    log(f"标签激活工具: [{tag_query}] -> {entity.name}", "DEBUG", tag="PFC")

    def activate_media_tools(self, images: list, media_segments: list) -> None:
        """按消息实际携带的媒体激活对应媒体工具（recognize_image / voice_to_text 等）。

        图片/媒体段是结构化字段而非文本标签（[media_type:*] 标签在入库时才生成），
        文本标签扫描覆盖不到，需按媒体对象显式激活。
        """
        if images:
            self.activate_by_tag("media:image")
        for seg in media_segments or []:
            seg_type = getattr(seg, "type", None)
            type_name = seg_type.value if hasattr(seg_type, "value") else str(seg_type or "")
            if type_name:
                self.activate_by_tag(f"media:{type_name}")

    # ==================================================================
    # 召回：热工具 / 频道 / 标签 / 动态发现
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
        """返回 top-N 热工具名（按命中次数选取，按名称排序返回保证字节序稳定）。"""
        if not self._tool_recall:
            return []
        sorted_tools = sorted(self._tool_recall.items(), key=lambda x: x[1], reverse=True)
        hot = sorted(name for name, _ in sorted_tools[:self._tool_recall_top_n])
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
        return EntityRegistry.get_tool_schema_by_names(sorted(self._tag_activated_tools))

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
                        self._tools_version += 1
                        log(f"动态发现工具: {name} (来自分组 {group})", "DEBUG", tag="PFC")
            except Exception as e:
                log(f"动态工具发现失败: {e}", "DEBUG", tag="PFC")

    def clear_dynamic_tools(self, scope: str = "") -> None:
        """清除当轮动态工具状态（tag 激活 + 动态发现）。

        scope 非空时仅清除该 scope 相关的状态（后台评审等并行会话不踩踏主会话）。
        当前实现：动态工具是全局共享的（tag/discovered 不按 scope 分桶），
        因此仅在 scope 为空时执行全量清理；调用方应在 active_scopes 清空后再清。

        粘性模式（tool_dynamic_sticky，默认开）：保留 tag 激活与动态发现——
        它们是消息内容驱动的（如图片到达激活媒体工具），清掉会导致下个会话
        重新激活、工具集在两个状态间反复抖动；tools 数组位于请求最前，
        任何字节变化都会击穿其后的全部前缀缓存（实测单次重写 ~30K tokens）。
        进程生命周期内工具集只增不减 + 确定性排序 = 跨会话字节稳定。
        """
        if scope:
            # 非主会话（如后台评审 reflect）：不清理全局动态工具，避免踩踏正在进行的对话
            return
        from core.config import get_config_bool
        if get_config_bool("tool_dynamic_sticky", True):
            return
        self._tag_activated_tools.clear()
        self._discovered_tools.clear()
        self._tools_version += 1

    # ==================================================================
    # 活跃工具集合并
    # ==================================================================

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
                sorted(self._discovered_tools)), "discovered")

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

        # 相关性排序：核心流程工具 → 高频工具（按使用次数降序）→ 其余按名称
        all_schemas.sort(key=self._tool_sort_key)

        sources = ", ".join(f"{k}={v}" for k, v in source_counts.items())
        tool_names = [s.get("function", {}).get("name", "") for s in all_schemas]
        log(f"活跃工具集: {len(all_schemas)} 个 ({sources}) [{', '.join(tool_names)}]", "DEBUG", tag="PFC")

        return all_schemas

    # 核心流程工具固定优先级（排序最前）
    _CORE_TOOL_PRIORITY: dict[str, int] = {
        "end_reply": 0, "send_message": 1,
    }

    def _tool_sort_key(self, schema: dict) -> tuple:
        """工具排序键：核心流程 → 其余按名称（确定性模式）/ 已使用分层（兼容模式）。

        确定性模式（tool_order_deterministic，默认开）：排序与使用计数完全无关，
        同一工具集在任何会话、任何时刻产出字节级一致的 tools 数组——
        tools schema 通常是 prompt 的最大头，其跨会话稳定性直接决定
        provider 前缀缓存命中率上限。会话内稳定性由 think_loop 冻结排序保证。

        兼容模式：核心流程 → 已使用工具 → 其余（层内均按名称）。
        """
        name = schema.get("function", {}).get("name", "")
        if name in self._CORE_TOOL_PRIORITY:
            return (0, self._CORE_TOOL_PRIORITY[name])
        from core.config import get_config_bool
        if get_config_bool("tool_order_deterministic", True):
            return (1, name)
        if self._tool_recall.get(name, 0) > 0:
            return (1, name)
        return (2, name)

    @staticmethod
    def _is_sleeping_tool(tool_name: str, sleepable_groups: dict, scope: str) -> bool:
        """判断工具当前是否处于沉睡状态（可沉睡且所属分组未激活）。"""
        from agent.mind.tool_activation import tool_activation
        entity = EntityRegistry.get(tool_name)
        if entity is None or not (entity.allow_sleep and entity.sleep_brief):
            return False
        return not tool_activation.is_active(entity.group, scope)

    # ==================================================================
    # 监控
    # ==================================================================

    def get_tool_recall_sorted(self) -> list[tuple[str, int]]:
        """工具命中计数降序列表（状态快照用）。"""
        return sorted(self._tool_recall.items(), key=lambda x: x[1], reverse=True)

    @property
    def tag_activated_tools(self) -> set[str]:
        return self._tag_activated_tools

    @property
    def discovered_tools(self) -> set[str]:
        return self._discovered_tools
