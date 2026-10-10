"""ToolAssembly — 工具装配：召回、tag 激活、动态发现、schema 合并与门控。

从 PrefrontalCortex 拆分而来。本模块为自包含状态类，不依赖 Mind/PFC，
工具实体、门控与频道声明通过各自公共接口读取，便于独立测试。

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
        self._scope_discovered_tools: dict[str, set[str]] = {}
        self._scope_frozen_tool_names: dict[str, list[str]] = {}
        self._scope_order: dict[str, None] = {}
        # 动态工具集版本号（tag 激活/动态发现变化时递增，供 think_loop 检测重建）
        self._tools_version: int = 0
        # 跨回复冻结的 tools 数组顺序（追加式：只增不改，缓存前缀稳定的最终防线；
        # 仅存名字，注销工具由存在性检查自然滤除，无害残留）
        self._frozen_tool_names: list[str] = []

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
            type_name = seg_type.value if seg_type is not None and hasattr(seg_type, "value") else str(seg_type or "")
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

    def expand_discovered_tools(self, tool_calls: list, scope: str = "") -> None:
        """解析 list_entity_methods 调用结果，将发现的工具加入动态发现集。"""
        import json as _json
        self._touch_scope(scope)
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
                    if scope:
                        discovered = self._scope_discovered_tools.setdefault(scope, set())
                        if name not in discovered:
                            discovered.add(name)
                            self._tools_version += 1
                    elif name not in self._discovered_tools:
                        self._discovered_tools.add(name)
                        self._tools_version += 1
                        log(f"动态发现工具: {name} (来自分组 {group})", "DEBUG", tag="PFC")
            except Exception as e:
                log(f"动态工具发现失败: {e}", "DEBUG", tag="PFC")

    def _touch_scope(self, scope: str) -> None:
        """保留最近 128 个会话的工具目录，限制跨回复缓存占用。"""
        if not scope:
            return
        self._scope_order.pop(scope, None)
        self._scope_order[scope] = None
        while len(self._scope_order) > 128:
            oldest = next(iter(self._scope_order))
            self._scope_order.pop(oldest)
            self._scope_discovered_tools.pop(oldest, None)
            self._scope_frozen_tool_names.pop(oldest, None)

    def clear_dynamic_tools(self, scope: str = "", *, force: bool = False) -> None:
        """清理动态目录；长期会话可保留前缀，一次性反思强制释放自身状态。"""
        from core.config import get_config_bool
        if not force and get_config_bool("tool_dynamic_sticky", True):
            return
        if scope:
            self._scope_discovered_tools.pop(scope, None)
            self._scope_frozen_tool_names.pop(scope, None)
            self._scope_order.pop(scope, None)
            self._tools_version += 1
            return
        self._tag_activated_tools.clear()
        self._discovered_tools.clear()
        self._scope_discovered_tools.clear()
        self._scope_frozen_tool_names.clear()
        self._scope_order.clear()
        self._frozen_tool_names.clear()
        self._tools_version += 1

    # ==================================================================
    # 活跃工具集合并
    # ==================================================================

    async def get_active_tool_schemas(self, adapter_key: str = "", scope: str = "") -> list[dict]:
        """合并返回当前所有活跃工具 schema（always + 频道 + 标签 + 热召回 + 动态发现 + 已激活分组）。

        合并结果经两道门控过滤：
        1. 沉睡过滤：已激活分组和频道显式声明的分组直接可见，其余遵循沉睡状态
        2. check_fn 门控：前置条件不满足的工具被过滤（core.tool_gate）
        """
        from agent.channel.reply_policy import get_reply_policy
        from agent.mind.tool_activation import tool_activation

        self._touch_scope(scope)
        policy = get_reply_policy(adapter_key, self._channel_manager)
        if policy.initial_tools is not None:
            return await self._get_scoped_tool_schemas(policy.initial_tools, policy.tool_groups, scope)

        seen_names: set[str] = set()
        all_schemas: list[dict] = []
        source_counts: dict[str, int] = {}
        # 作用域相关工具（频道能力/tag 激活/scope 激活分组）名集合：
        # 排序时沉到共享核心工具之后，让不同 scope 的 tools 数组共享最长
        # 公共头部（隐式前缀缓存按字节前缀匹配——QQ 与 webui 的工具集差异
        # 若在头部出现，跨 scope 命中会在第一个差异工具处截断）
        scoped_names: set[str] = set()

        def _merge(schemas: list[dict], source: str, *, scoped: bool = False) -> None:
            added = 0
            for s in schemas:
                name = s.get("function", {}).get("name", "")
                if name and name not in seen_names:
                    seen_names.add(name)
                    all_schemas.append(s)
                    if scoped:
                        scoped_names.add(name)
                    added += 1
            if added:
                source_counts[source] = added

        _merge(EntityRegistry.get_tool_schema_by_tags(["always"]), "always")

        awake_names: set[str] = set()
        for group in policy.tool_groups:
            schemas = EntityRegistry.get_tool_schemas_by_group(group)
            awake_names.update(s.get("function", {}).get("name", "") for s in schemas)
            _merge(schemas, f"channel_group:{group}", scoped=True)

        if adapter_key:
            _merge(self.get_channel_tool_schemas(adapter_key), f"channel:{adapter_key}", scoped=True)
            _merge(EntityRegistry.get_tool_schema_by_tags([adapter_key]),
                   f"channel_tag:{adapter_key}", scoped=True)

        _merge(self.resolve_tag_tool_schemas(), "tag_match")
        _merge(self.get_hot_tool_schemas(), "hot_recall")

        discovered = self._scope_discovered_tools.get(scope, set()) if scope else self._discovered_tools
        if discovered:
            _merge(EntityRegistry.get_tool_schema_by_names(
                sorted(discovered)), "discovered", scoped=True)

        # 已激活的沉睡分组：补充其全部工具（即使未被上述渠道命中）
        activated = tool_activation.active_groups(scope)
        for group in activated:
            _merge(EntityRegistry.get_tool_schemas_by_group(group), f"activated:{group}", scoped=True)

        # 冻结结转：历史冻结工具仍注册在案则并回候选——热召回 top-N 换血、
        # tag/发现集变化不再导致数组元素消失（消失会把缓存前缀在该位置截断）；
        # 沉睡过滤与 check_fn 门控在下方照常适用于结转工具
        frozen = self._scope_frozen_tool_names.setdefault(scope, []) if scope else self._frozen_tool_names
        if frozen:
            carryover = sorted(n for n in frozen if n not in seen_names)
            if carryover:
                _merge(
                    EntityRegistry.get_tool_schema_by_names(carryover),
                    "frozen_carryover",
                )

        # 门控过滤（沉睡 + check_fn，与反思目录共用同一设施）
        before = len(all_schemas)
        all_schemas = await self._apply_tool_gates(all_schemas, scope, awake_names)
        slept = before - len(all_schemas)
        if slept:
            source_counts["gated"] = -slept

        # 排序：追加式冻结（默认）保证回复间字节稳定；否则按双桶排序键
        from core.config import get_config_bool
        deterministic = get_config_bool("tool_order_deterministic", True)
        if get_config_bool("tool_order_frozen", True) and deterministic:
            all_schemas = self._apply_append_only_freeze(all_schemas, scoped_names, frozen_names=frozen)
        else:
            # 配置在排序前读一次，排序键不再逐元素读取
            all_schemas.sort(
                key=lambda s: self._tool_sort_key(s, scoped_names, deterministic=deterministic)
            )

        sources = ", ".join(f"{k}={v}" for k, v in source_counts.items())
        tool_names = [s.get("function", {}).get("name", "") for s in all_schemas]
        log(f"活跃工具集: {len(all_schemas)} 个 ({sources}) [{', '.join(tool_names)}]", "DEBUG", tag="PFC")

        return all_schemas

    async def _get_scoped_tool_schemas(
        self, initial_tools: tuple[str, ...], tool_groups: tuple[str, ...], scope: str,
    ) -> list[dict]:
        """频道声明的精简目录，仅由同 scope 显式发现/激活扩展，门控仍然生效。"""
        from agent.mind.tool_activation import tool_activation
        from core.config import get_config_bool

        names = set(initial_tools) | self._scope_discovered_tools.get(scope, set())
        for group in tool_activation.active_groups(scope):
            names.update(s["function"]["name"] for s in EntityRegistry.get_tool_schemas_by_group(group))
        awake_names = {
            s["function"]["name"] for group in tool_groups
            for s in EntityRegistry.get_tool_schemas_by_group(group)
            if s["function"]["name"] in names
        }
        schemas = await self._apply_tool_gates(
            EntityRegistry.get_tool_schema_by_names(sorted(names)), scope, awake_names,
        )
        deterministic = get_config_bool("tool_order_deterministic", True)
        if deterministic and get_config_bool("tool_order_frozen", True):
            schemas = self._apply_append_only_freeze(
                schemas, set(), frozen_names=self._scope_frozen_tool_names.setdefault(scope, []),
            )
        else:
            schemas.sort(key=lambda s: self._tool_sort_key(s, deterministic=deterministic))
        log(f"会话工具集: {len(schemas)} 个 (scope={scope})", "DEBUG", tag="PFC")
        return schemas

    # 反思/任务循环未指定 tool_tags 时的默认选择器（心跳任务常态工具面）
    REFLECT_DEFAULT_SELECTORS: tuple[str, ...] = ("heartbeat",)

    async def get_reflect_tool_schemas(
        self,
        adapter_key: str = "",
        scope: str = "",
        selectors: Optional[list[str]] = None,
    ) -> list[dict]:
        """反思循环（心跳任务/子代理/元决策）的精简工具目录。

        构成 = always 常态工具 + 频道工具（有 adapter 时）+ 选择器匹配
        （默认 heartbeat 标签；兼容 group 名与 mcp: 简写）+ 动态发现与已
        激活分组（自服务扩展）。刻意不含回复级的热召回与冻结结转——那是
        跨回复前缀缓存状态，反思 scope 一次性、无结转价值；精简目录显著
        降低高频内部调用的 schema 开销。显式 selectors 匹配的工具在本 scope
        直接唤醒，默认 heartbeat 不改变沉睡状态；更多分组由模型经
        list_entity_methods / activate_tool_group（均为 always 工具）按需
        唤醒，工具分组目录在 stable 提示中常驻可见。
        """
        from agent.mind.tool_activation import tool_activation

        seen_names: set[str] = set()
        all_schemas: list[dict] = []
        scoped_names: set[str] = set()

        def _merge(schemas: list[dict], *, scoped: bool = False) -> None:
            for s in schemas:
                name = s.get("function", {}).get("name", "")
                if name and name not in seen_names:
                    seen_names.add(name)
                    all_schemas.append(s)
                    if scoped:
                        scoped_names.add(name)

        _merge(EntityRegistry.get_tool_schema_by_tags(["always"]))

        if adapter_key:
            _merge(self.get_channel_tool_schemas(adapter_key), scoped=True)
            _merge(EntityRegistry.get_tool_schema_by_tags([adapter_key]), scoped=True)

        awake_names: set[str] = set()
        effective = selectors if selectors else list(self.REFLECT_DEFAULT_SELECTORS)
        for selector in effective:
            sel = (selector or "").strip()
            if not sel:
                continue
            # 1) 先按 tag 匹配，2) 再按 group 匹配（含 mcp: 简写）
            selected = EntityRegistry.get_tool_schema_by_tags([sel])
            groups = [sel] if ":" in sel else [sel, f"mcp:{sel}"]
            for group in groups:
                selected.extend(EntityRegistry.get_tool_schemas_by_group(group))
            _merge(selected)
            if selectors:
                awake_names.update(s.get("function", {}).get("name", "") for s in selected)

        # 自服务扩展：list_entity_methods 动态发现 + activate_tool_group 唤醒
        self._touch_scope(scope)
        discovered = self._scope_discovered_tools.get(scope, set()) if scope else self._discovered_tools
        if discovered:
            _merge(EntityRegistry.get_tool_schema_by_names(
                sorted(discovered)), scoped=True)
        for group in tool_activation.active_groups(scope):
            _merge(EntityRegistry.get_tool_schemas_by_group(group), scoped=True)

        all_schemas = await self._apply_tool_gates(all_schemas, scope, awake_names)

        # 确定性排序（与回复同一排序键，目录跨调用字节稳定）；不使用回复级
        # 冻结结转——避免把回复的冻结历史重新引入精简目录
        from core.config import get_config_bool
        deterministic = get_config_bool("tool_order_deterministic", True)
        all_schemas.sort(
            key=lambda s: self._tool_sort_key(s, scoped_names, deterministic=deterministic)
        )

        tool_names = [s.get("function", {}).get("name", "") for s in all_schemas]
        log(f"反思工具集: {len(all_schemas)} 个 (selectors={effective}) [{', '.join(tool_names)}]", "DEBUG", tag="PFC")
        return all_schemas

    async def _apply_tool_gates(
        self, all_schemas: list[dict], scope: str, awake_names: set[str] | None = None,
    ) -> list[dict]:
        """门控过滤（回复与反思两条装配路径共用）：沉睡过滤 + check_fn 前置条件。"""
        # 沉睡过滤：移除未激活分组中的可沉睡工具
        sleepable_groups = EntityRegistry.get_sleepable_groups()
        if sleepable_groups:
            all_schemas = [
                s for s in all_schemas
                if s.get("function", {}).get("name", "") in (awake_names or set()) or not self._is_sleeping_tool(
                    s.get("function", {}).get("name", ""), sleepable_groups, scope,
                )
            ]
        # check_fn 门控过滤
        names = [s.get("function", {}).get("name", "") for s in all_schemas]
        active_entities = await EntityRegistry.get_active_tools(names)
        active_names = {e.name for e in active_entities}
        return [
            s for s in all_schemas
            if s.get("function", {}).get("name", "") in active_names
        ]

    # 核心流程工具固定优先级（同桶内排序最前）
    _CORE_TOOL_PRIORITY: dict[str, int] = {
        "end_reply": 0, "send_message": 1,
    }

    def _tool_sort_key(
        self,
        schema: dict,
        scoped_names: frozenset | set = frozenset(),
        *,
        deterministic: bool = True,
    ) -> tuple:
        """工具排序键：共享核心桶 → 作用域桶；桶内核心流程优先、其余按名称。

        确定性模式（tool_order_deterministic，默认开）：排序与使用计数完全无关，
        同一工具集在任何会话、任何时刻产出字节级一致的 tools 数组——
        tools schema 通常是 prompt 的最大头，其跨会话稳定性直接决定
        provider 前缀缓存命中率上限。会话内稳定性由 think_loop 冻结排序保证。

        双桶设计：作用域相关工具（频道能力/scope 激活分组，scoped_names）
        沉到共享核心工具之后——不同 scope 的 tools 数组只在尾部不同，
        隐式前缀缓存（DeepSeek/OpenAI）的跨 scope 命中延伸到整个共享头部，
        而不是在第一个频道工具处截断（实测位置 1 分叉 = 新会话仅命中 ~30%）。

        兼容模式：核心流程 → 已使用工具 → 其余（层内均按名称），不分桶。
        """
        name = schema.get("function", {}).get("name", "")
        if not deterministic:
            if name in self._CORE_TOOL_PRIORITY:
                return (0, self._CORE_TOOL_PRIORITY[name])
            if self._tool_recall.get(name, 0) > 0:
                return (1, name)
            return (2, name)
        bucket = 1 if name in scoped_names else 0
        return (bucket, self._CORE_TOOL_PRIORITY.get(name, 1), name)

    def _apply_append_only_freeze(
        self, schemas: list[dict], scoped_names: set, *, frozen_names: list[str] | None = None,
    ) -> list[dict]:
        """跨回复追加式冻结 tools 数组顺序（缓存前缀稳定的最终防线）。

        首轮（冻结名单为空）按双桶排序键建立冻结序；此后每次组装：
        仍在本轮活跃集中的冻结工具按冻结序输出，新工具按（桶/核心优先级/
        名称）排序追加尾部——数组只增不改，热召回 top-N 换血、tag/发现集
        变化都不再改变已有前缀字节（实测热工具换血会把每轮新回复的缓存
        命中截断在固定位置）。工具注销/门控排除由存在性检查自然滤除。
        """
        by_name: dict[str, dict] = {}
        for s in schemas:
            name = s.get("function", {}).get("name", "")
            if name:
                by_name[name] = s
        frozen = self._frozen_tool_names if frozen_names is None else frozen_names
        frozen_set = set(frozen)
        ordered = [by_name[n] for n in frozen if n in by_name]
        newcomers = sorted(
            (n for n in by_name if n not in frozen_set),
            key=lambda n: (
                1 if n in scoped_names else 0,
                self._CORE_TOOL_PRIORITY.get(n, 1),
                n,
            ),
        )
        frozen += newcomers
        return ordered + [by_name[n] for n in newcomers]

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
        return self._discovered_tools.union(*self._scope_discovered_tools.values())
