"""上下文提供者注册表 — 实体向 PFC volatile 层注入实时数据。

设计哲学：provider 是自治生产者，快照必须零 I/O 读内存（1s 硬超时兜底）；
消费方（think_loop 每轮发送组装）经 collect() 取**当前最新**快照——缓存
超过新鲜度阈值即内联并发重收（成本有界），保证时间类实时信息逐轮新鲜。

实体开发者只需：
1. 用 @context_provider 装饰器注册（见 entities/_sdk.py），
   并以 group 声明所属工具分组、以 inject_key 声明注入开关
2. 实现 provide() 方法返回 ProviderSnapshot
3. 可选实现 on_start / on_tick / on_stop 生命周期

启停门控（两道，均热读取）：
1. 注入开关：provider 以 inject_key 声明配置键（约定 <组名>_context_inject），
   配置为 False 时停止采集与注入——任何上下文注入都必须有此开关，
   声明后配置中心/实体配置 tab 自动出现（_sdk 装饰器兜底注册）；
2. 实体启停：声明了 group 的 provider 随实体启停联动——分组内全部工具
   被禁用（实体目录中该分组同步消失）时停止采集与注入；重新启用自动恢复。
   未声明 group 的 provider 视为全局常驻。

Model Experience：
- 模型看到什么：volatile 尾部动态区的实体状态快照（每条快照一条 system 消息）；
  实体分组全禁用后对应快照消失，部分禁用（分组在目录中仍可见）不影响注入
- token 影响：禁用后每轮节省该 provider 的快照 tokens（上限为其 max_tokens）
- 缓存影响：注入块位于 volatile 层（对话历史之后），不触碰 stable/summary/
  conversation 前缀层，启停切换不击穿前缀缓存

预算与监督：
- 总预算（token）由 collect() 的 budget 参数控制，超限日志警告 + 截断
- 每次 provide() 的耗时、字节数、token 数记录到 ProviderMetric
- get_status() 供 Web API 展示预算占用、峰值、每个 provider 指标
"""

from __future__ import annotations

import asyncio
import inspect
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

from core.log import log

# collect() 与 get_status() 共用的默认 token 预算
DEFAULT_COLLECT_BUDGET = 8000
# 单个 provider 快照的采集超时（秒）
_PROVIDE_TIMEOUT_SECONDS = 1.0
# 快照新鲜度阈值（秒）：collect 时缓存超过该时长即内联并发重收，
# 保证时间等实时信息逐轮新鲜；阈值内复用缓存避免单轮内重复收集
_COLLECT_FRESH_SECONDS = 2.0
# 字符串长度 -> token 数的粗估除数
_CHARS_PER_TOKEN = 4
# 按 scope 统计状态（metrics/collect/peak/snippets）的容量上限，超出按 LRU 淘汰
_MAX_TRACKED_SCOPES = 200


# ======================================================================
# 数据模型
# ======================================================================


@dataclass
class ProviderSnapshot:
    """实体提供给 PFC 的一次快照。

    Attributes:
        content: 注入文本。None 或空字符串表示本轮不注入。
        ready: False 表示实体尚未加载完成（可选注入占位文案）。
        tokens: 实体自报 token 数（用于预算计算）。
        bytes: 实际字节数（用于监控展示）。
        fetched_at: 快照生成时间戳（time.time()）。
        default_when_not_ready: ready=False 时的兜底文案（空则跳过）。
    """

    content: Optional[str] = None
    ready: bool = True
    tokens: int = 0
    bytes: int = 0
    fetched_at: float = 0.0
    default_when_not_ready: str = ""


@dataclass
class ProviderMetric:
    """单次 provide() 的监督指标。"""

    name: str
    tokens: int = 0
    bytes: int = 0
    cost_ms: float = 0.0
    ready: bool = True
    fetched_at: float = 0.0
    last_error: str = ""
    call_count: int = 0


@dataclass
class ProviderMeta:
    """注册元数据。

    Attributes:
        name: 提供者唯一标识。
        priority: 注入优先级（越小越靠前，预算超限时大值先被截断）。语义为
            变动率排序，与上下文管线 volatility 教义同构——快照越静态越小
            （注入越靠前），含时间/秒计数等逐轮变化内容的实时快照越大
            （越靠尾部动态区末尾，贴近注意力最强处）。段位约定：
            10-19 状态级（连接/解锁/在线清单，仅状态翻转时变化）、
            20-29 摘要级（低频刷新的计数/名单摘要）、30-39 会话操作态势
            （随本会话操作实时变化）、40+ 实时快照（含时间等逐轮变化内容）。
        max_tokens: 静态预估上限（Web 展示 + 预算告警参考）。
        scope_filter: 作用域过滤。None=全局；"webui:*"=前缀匹配；"webui:u123"=精确匹配。
        group: 所属工具分组（实体启停门控依据）。None=全局常驻，不随实体启停。
        inject_key: 注入开关配置键（约定 <组名>_context_inject）。声明后该配置
            为 False 时停止采集与注入；None 表示无开关（仅限永不注入的
            生命周期钩子型 provider）。
        instance: 实体实例（类模式，有生命周期）。
        provide_fn: 函数式 provide（函数模式，无生命周期）。
        description: 描述（Web 展示用）。
    """

    name: str
    priority: int = 50
    max_tokens: int = 500
    scope_filter: Optional[str] = None
    group: Optional[str] = None
    inject_key: Optional[str] = None
    instance: Any = None
    provide_fn: Optional[Callable] = None
    description: str = ""


# ======================================================================
# 注册表
# ======================================================================


class ContextProviderRegistry:
    """全局上下文提供者注册表。

    所有方法均为 classmethod，无需实例化。
    """

    _providers: Dict[str, ProviderMeta] = {}
    _call_counts: Dict[str, int] = {}
    _last_errors: Dict[str, str] = {}
    # provider 名 -> 最近一次实际注入的内容（跨 scope 取最新，供 Web 面板查看正文诊断）
    _last_contents: Dict[str, Dict[str, Any]] = {}
    # 按 scope 的统计状态（LRU，容量上限 _MAX_TRACKED_SCOPES）
    _last_metrics: "OrderedDict[str, List[ProviderMetric]]" = OrderedDict()
    _last_collect: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()
    _last_snippets: "OrderedDict[str, List[str]]" = OrderedDict()
    _peak: "OrderedDict[str, int]" = OrderedDict()
    # scope -> 进行中的后台收集任务（同一 scope 同时只有一个收集任务）
    _inflight: Dict[str, "asyncio.Task[None]"] = {}

    # ------------------------------------------------------------------
    # 注册 / 注销
    # ------------------------------------------------------------------

    @classmethod
    def register(cls, meta: ProviderMeta) -> None:
        """注册一个上下文提供者（同名覆盖）。"""
        cls._providers[meta.name] = meta
        cls._call_counts.setdefault(meta.name, 0)
        log(f"上下文提供者已注册: {meta.name} (priority={meta.priority})", "DEBUG", tag="Provider")

    @classmethod
    def unregister(cls, name: str) -> None:
        """注销一个上下文提供者。"""
        cls._providers.pop(name, None)
        cls._call_counts.pop(name, None)
        cls._last_errors.pop(name, None)
        cls._last_contents.pop(name, None)

    @classmethod
    def get_all(cls) -> List[ProviderMeta]:
        """获取所有已注册的提供者（按 priority 排序）。"""
        return sorted(cls._providers.values(), key=lambda m: m.priority)

    # ------------------------------------------------------------------
    # PFC 拉取（核心路径）
    # ------------------------------------------------------------------

    @classmethod
    async def collect(
        cls,
        scope: str = "",
        budget: int = DEFAULT_COLLECT_BUDGET,
    ) -> Tuple[List[str], List[ProviderMetric]]:
        """返回当前最新快照；缓存超过新鲜度阈值时内联并发重收。

        provider 契约是零 I/O 读内存快照（_safe_provide 有 1s 硬超时且
        并发执行），内联重收成本有界（通常亚毫秒）——时间等实时信息因此
        逐轮新鲜，不再滞后一轮。同 scope 并发调用经 inflight 去重。

        Args:
            scope: 当前对话 scope（用于过滤）。
            budget: token 预算上限，收集超限日志警告并截断。

        Returns:
            (snippets, metrics) — snippets 是注入文本列表，metrics 是监督指标。
        """
        last = cls._last_collect.get(scope, {})
        stale = (
            time.time() - float(last.get("collected_at", 0))
            > _COLLECT_FRESH_SECONDS
        )
        if stale:
            task = cls._inflight.get(scope)
            if task is None or task.done():
                task = asyncio.ensure_future(cls._collect_background(scope, budget))
                cls._inflight[scope] = task

                def _cleanup(done: "asyncio.Task[None]", key: str = scope) -> None:
                    if cls._inflight.get(key) is done:
                        del cls._inflight[key]

                task.add_done_callback(_cleanup)
            await task
        snippets = list(cls._last_snippets.get(scope, []))
        metrics = list(cls._last_metrics.get(scope, []))
        return snippets, metrics

    @classmethod
    def _bounded_put(cls, store: "OrderedDict[str, Any]", key: str, value: Any) -> None:
        """写入按 scope 统计的字典（LRU，超容量淘汰最久未用的条目）。"""
        store[key] = value
        store.move_to_end(key)
        while len(store) > _MAX_TRACKED_SCOPES:
            store.popitem(last=False)

    @classmethod
    async def _collect_background(cls, scope: str, budget: int) -> None:
        """收集一轮所有匹配 scope 的 provider 快照并写入缓存。

        全部 provider 并发采集（各自 1s 硬超时，总耗时有界 ≈1s），
        结果按注册优先级顺序拼装。任何异常记 DEBUG 不抛出。
        """
        try:
            metas = [
                meta for meta in cls.get_all()
                if cls._is_active(meta) and cls._match_scope(meta.scope_filter, scope)
            ]
            results = await asyncio.gather(
                *(cls._safe_provide(meta, scope) for meta in metas)
            )

            snippets: List[str] = []
            metrics: List[ProviderMetric] = []
            used_tokens = 0
            used_bytes = 0

            for meta, result in zip(metas, results, strict=True):
                if result is None:
                    continue
                snap, cost_ms = result

                # 未加载完：有兜底文案则注入占位，否则跳过
                if not snap.ready:
                    if snap.default_when_not_ready:
                        snippets.append(snap.default_when_not_ready)
                    continue

                # 空内容跳过
                if not snap.content:
                    continue

                # 预算检查
                if used_tokens + snap.tokens > budget:
                    log(
                        f"上下文注入超预算: {used_tokens + snap.tokens}/{budget} "
                        f"(provider={meta.name})",
                        "WARNING",
                        tag="Provider",
                    )
                    break

                snippets.append(snap.content)
                used_tokens += snap.tokens
                used_bytes += snap.bytes
                cls._last_contents[meta.name] = {
                    "content": snap.content,
                    "fetched_at": snap.fetched_at or time.time(),
                    "scope": scope,
                }

                metrics.append(ProviderMetric(
                    name=meta.name,
                    tokens=snap.tokens,
                    bytes=snap.bytes,
                    cost_ms=cost_ms,
                    ready=snap.ready,
                    fetched_at=snap.fetched_at,
                    last_error=cls._last_errors.get(meta.name, ""),
                    call_count=cls._call_counts.get(meta.name, 0),
                ))

            # 记录本次收集结果（供下一轮 collect 与 Web API 读取）
            cls._bounded_put(cls._last_snippets, scope, snippets)
            cls._bounded_put(cls._last_metrics, scope, metrics)
            cls._bounded_put(cls._last_collect, scope, {
                "used_tokens": used_tokens,
                "used_bytes": used_bytes,
                "total_budget": budget,
                "providers_count": len(metrics),
                "collected_at": time.time(),
            })
            # 更新峰值
            if used_tokens > cls._peak.get(scope, 0):
                cls._bounded_put(cls._peak, scope, used_tokens)
        except Exception as exc:
            log(f"上下文后台收集异常: scope={scope!r} - {exc}", "DEBUG", tag="Provider")

    @classmethod
    async def _safe_provide(
        cls,
        meta: ProviderMeta,
        scope: str,
    ) -> Optional[Tuple[ProviderSnapshot, float]]:
        """安全调用 provider 的 provide()，超时/异常静默跳过。

        Returns:
            (snapshot, cost_ms) 或 None。
        """
        start = time.perf_counter()
        try:
            if meta.instance is not None:
                # 类模式：调用实例的 provide 方法
                provide = getattr(meta.instance, "provide", None)
                if provide is None:
                    return None
            elif meta.provide_fn is not None:
                # 函数模式
                provide = meta.provide_fn
            else:
                return None

            if asyncio.iscoroutinefunction(provide):
                result = await asyncio.wait_for(provide(scope), timeout=_PROVIDE_TIMEOUT_SECONDS)
            else:
                # 同步 provider 在线程中执行，避免阻塞事件循环
                result = await asyncio.wait_for(
                    asyncio.to_thread(provide, scope), timeout=_PROVIDE_TIMEOUT_SECONDS,
                )
                if inspect.isawaitable(result):
                    result = await asyncio.wait_for(result, timeout=_PROVIDE_TIMEOUT_SECONDS)

            cost_ms = (time.perf_counter() - start) * 1000
            cls._call_counts[meta.name] = cls._call_counts.get(meta.name, 0) + 1

            # 函数模式可能直接返回 str
            if isinstance(result, str):
                snap = ProviderSnapshot(
                    content=result or None,
                    tokens=len(result) // _CHARS_PER_TOKEN if result else 0,
                    bytes=len(result.encode("utf-8")) if result else 0,
                    fetched_at=time.time(),
                )
            elif isinstance(result, ProviderSnapshot):
                snap = result
                # 自动补充字节数
                if snap.content and snap.bytes == 0:
                    snap.bytes = len(snap.content.encode("utf-8"))
                # 未自报 tokens 时按内容长度粗估（与函数模式同口径，
                # 供预算约束与 Web 面板占用统计；否则快照模式恒为 0）
                if snap.content and snap.tokens <= 0:
                    snap.tokens = max(1, len(snap.content) // _CHARS_PER_TOKEN)
                if snap.fetched_at == 0.0:
                    snap.fetched_at = time.time()
            else:
                return None

            return snap, cost_ms

        except asyncio.TimeoutError:
            cost_ms = (time.perf_counter() - start) * 1000
            cls._last_errors[meta.name] = f"timeout ({cost_ms:.0f}ms)"
            log(f"上下文提供者超时: {meta.name} ({cost_ms:.0f}ms)", "WARNING", tag="Provider")
            return None
        except Exception as exc:
            cls._last_errors[meta.name] = str(exc)
            log(f"上下文提供者异常: {meta.name} - {exc}", "DEBUG", tag="Provider")
            return None

    @classmethod
    def _is_active(cls, meta: ProviderMeta) -> bool:
        """provider 是否处于注入活动状态。

        两道门控（均热读取，配置/启停变更即时生效）：
        1. 注入开关：声明 inject_key 且配置为 False → 停止采集与注入；
        2. 实体启停：声明 group 且分组内全部工具被禁用（实体目录中该分组
           同步消失）→ 停止采集与注入。启停事实的唯一权威是 EntityRegistry。
        """
        if meta.inject_key is not None:
            from core.config import get_config_bool
            if not get_config_bool(meta.inject_key, True):
                return False
        if meta.group is None:
            return True
        from core.entity import EntityRegistry
        return EntityRegistry.group_has_enabled_tools(meta.group)

    @classmethod
    def _match_scope(cls, scope_filter: Optional[str], scope: str) -> bool:
        """检查 scope 是否匹配 provider 的过滤规则。"""
        if scope_filter is None:
            return True
        if scope_filter.endswith("*"):
            return scope.startswith(scope_filter[:-1])
        return scope == scope_filter

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    @classmethod
    async def start_all(cls) -> None:
        """触发所有有 on_start 的 provider（bootstrap 末尾调用）。"""
        for meta in cls.get_all():
            if meta.instance is None:
                continue
            on_start = getattr(meta.instance, "on_start", None)
            if on_start is None:
                continue
            try:
                await on_start()
                log(f"上下文提供者已启动: {meta.name}", "DEBUG", tag="Provider")
            except Exception as exc:
                log(f"上下文提供者启动失败: {meta.name} - {exc}", "WARNING", tag="Provider")

    @classmethod
    async def stop_all(cls) -> None:
        """触发所有有 on_stop 的 provider（shutdown 时调用）。"""
        for meta in cls.get_all():
            if meta.instance is None:
                continue
            on_stop = getattr(meta.instance, "on_stop", None)
            if on_stop is None:
                continue
            try:
                await on_stop()
            except Exception as exc:
                log(f"上下文提供者停止失败: {meta.name} - {exc}", "DEBUG", tag="Provider")

    @classmethod
    async def tick_all(cls) -> None:
        """触发所有有 on_tick 的 provider（心跳 tick 时调用）。"""
        for meta in cls.get_all():
            if meta.instance is None:
                continue
            on_tick = getattr(meta.instance, "on_tick", None)
            if on_tick is None:
                continue
            try:
                await on_tick()
            except Exception as exc:
                log(f"上下文提供者 tick 失败: {meta.name} - {exc}", "DEBUG", tag="Provider")

    # ------------------------------------------------------------------
    # Web API 状态
    # ------------------------------------------------------------------

    @classmethod
    def get_status(cls, scope: str = "") -> Dict[str, Any]:
        """返回预算占用、峰值、每个 provider 的指标（供 Web API 使用）。

        收集按真实会话 scope 分桶（user_qq:xxx / reflect:xxx 等），Web 面板
        不带 scope：取全 scope 中时间最近的一次收集（读 "" 桶只会拿到
        陈旧/零值）；峰值取全 scope 历史最大。响应携带数据归属的 scope
        与收集时间，供面板标注。
        """
        # 静态预估：所有 provider 的 max_tokens 之和
        static_estimate = sum(m.max_tokens for m in cls._providers.values())

        if scope:
            effective_scope = scope
        else:
            # 全 scope 最近一次收集（无收集记录时回退 "" 桶）
            effective_scope = ""
            latest_ts = -1.0
            for s, info in cls._last_collect.items():
                ts = float(info.get("collected_at", 0))
                if ts > latest_ts:
                    latest_ts, effective_scope = ts, s

        collect_info = cls._last_collect.get(effective_scope, {})
        current_used = collect_info.get("used_tokens", 0)
        total_budget = collect_info.get("total_budget", DEFAULT_COLLECT_BUDGET)

        # 峰值：指定 scope 取其峰值；面板口径取全 scope 历史最大
        if scope:
            peak = cls._peak.get(scope, 0)
        else:
            peak = max(cls._peak.values(), default=0)

        # 每个 provider 的指标
        provider_metrics = []
        for meta in cls.get_all():
            last_content = cls._last_contents.get(meta.name)
            provider_metrics.append({
                "name": meta.name,
                "priority": meta.priority,
                "max_tokens": meta.max_tokens,
                "scope_filter": meta.scope_filter,
                "group": meta.group,
                "inject_key": meta.inject_key,
                "active": cls._is_active(meta),
                "description": meta.description,
                "tokens": 0,
                "bytes": 0,
                "cost_ms": 0.0,
                "ready": True,
                "fetched_at": 0.0,
                "last_error": cls._last_errors.get(meta.name, ""),
                "call_count": cls._call_counts.get(meta.name, 0),
                # 最近一次收集中是否实际注入了内容（False=已注册但未注入，如直播模式关闭）
                "injecting": False,
                "injected_at": 0.0,
                # 最近一次实际注入的正文（跨 scope 取最新，供面板展开查看）
                "last_content": last_content["content"] if last_content else None,
                "last_content_at": last_content["fetched_at"] if last_content else 0.0,
                "last_content_scope": last_content["scope"] if last_content else "",
            })

        # 用最近一次 collect 的 metrics 覆盖实际值
        last_metrics = cls._last_metrics.get(effective_scope, [])
        metrics_map = {m.name: m for m in last_metrics}
        for pm in provider_metrics:
            if pm["name"] in metrics_map:
                m = metrics_map[pm["name"]]
                pm["tokens"] = m.tokens
                pm["bytes"] = m.bytes
                pm["cost_ms"] = m.cost_ms
                pm["ready"] = m.ready
                pm["fetched_at"] = m.fetched_at
                # metrics 仅在实际产出内容时登记（provide 返回空/None 不在其中）
                pm["injecting"] = True
                pm["injected_at"] = m.fetched_at

        return {
            "total_budget": total_budget,
            "static_estimate": static_estimate,
            "current_used": current_used,
            "peak_used": peak,
            "providers": provider_metrics,
            # 面板口径：数据归属的 scope 与该次收集时间（无收集记录时为空/0）
            "scope": effective_scope,
            "collected_at": float(collect_info.get("collected_at", 0)),
        }

    # ------------------------------------------------------------------
    # 清理（测试用）
    # ------------------------------------------------------------------

    @classmethod
    def reset(cls) -> None:
        """清空所有注册（测试用）。"""
        for task in cls._inflight.values():
            task.cancel()
        cls._inflight.clear()
        cls._providers.clear()
        cls._call_counts.clear()
        cls._last_errors.clear()
        cls._last_contents.clear()
        cls._last_metrics.clear()
        cls._last_collect.clear()
        cls._last_snippets.clear()
        cls._peak.clear()
