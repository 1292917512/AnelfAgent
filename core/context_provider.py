"""上下文提供者注册表 — 实体向 PFC volatile 层注入实时数据。

设计哲学：PFC 是被动消费者，实体是自治生产者。
实体通过 RunTimeline 自驱更新内部快照，PFC 每轮构建 volatile 层时
调用 collect() 拉取所有 provider 的最新快照。

实体开发者只需：
1. 用 @context_provider 装饰器注册（见 entities/_sdk.py）
2. 实现 provide() 方法返回 ProviderSnapshot
3. 可选实现 on_start / on_tick / on_stop 生命周期

预算与监督：
- 总预算（token）由 collect() 的 budget 参数控制，超限日志警告 + 截断
- 每次 provide() 的耗时、字节数、token 数记录到 ProviderMetric
- get_status() 供 Web API 展示预算占用、峰值、每个 provider 指标
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple, Tuple

from core.log import log


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
        priority: 注入优先级（越小越优先，预算超限时低优先级先被截断）。
        max_tokens: 静态预估上限（Web 展示 + 预算告警参考）。
        scope_filter: 作用域过滤。None=全局；"webui:*"=前缀匹配；"webui:u123"=精确匹配。
        instance: 实体实例（类模式，有生命周期）。
        provide_fn: 函数式 provide（函数模式，无生命周期）。
        description: 描述（Web 展示用）。
    """

    name: str
    priority: int = 50
    max_tokens: int = 500
    scope_filter: Optional[str] = None
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
    _last_metrics: Dict[str, List[ProviderMetric]] = {}
    _last_collect: Dict[str, Dict[str, Any]] = {}
    _peak: Dict[str, int] = {}

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
        budget: int = 2000,
    ) -> Tuple[List[str], List[ProviderMetric]]:
        """按 priority 排序拉取所有匹配 scope 的 provider 快照。

        Args:
            scope: 当前对话 scope（用于过滤）。
            budget: token 预算上限，超限日志警告并截断。

        Returns:
            (snippets, metrics) — snippets 是注入文本列表，metrics 是监督指标。
        """
        snippets: List[str] = []
        metrics: List[ProviderMetric] = []
        used_tokens = 0
        used_bytes = 0

        for meta in cls.get_all():
            if not cls._match_scope(meta.scope_filter, scope):
                continue

            result = await cls._safe_provide(meta, scope)
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

        # 记录本次收集结果（供 Web API 读取）
        cls._last_metrics[scope] = metrics
        cls._last_collect[scope] = {
            "used_tokens": used_tokens,
            "used_bytes": used_bytes,
            "total_budget": budget,
            "providers_count": len(metrics),
        }
        # 更新峰值
        prev_peak = cls._peak.get(scope, 0)
        if used_tokens > prev_peak:
            cls._peak[scope] = used_tokens

        return snippets, metrics

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
                result = await asyncio.wait_for(provide(scope), timeout=1.0)
            elif meta.provide_fn is not None:
                # 函数模式
                result = await asyncio.wait_for(meta.provide_fn(scope), timeout=1.0)
            else:
                return None

            cost_ms = (time.perf_counter() - start) * 1000
            cls._call_counts[meta.name] = cls._call_counts.get(meta.name, 0) + 1

            # 函数模式可能直接返回 str
            if isinstance(result, str):
                snap = ProviderSnapshot(
                    content=result or None,
                    tokens=len(result) // 4 if result else 0,
                    bytes=len(result.encode("utf-8")) if result else 0,
                    fetched_at=time.time(),
                )
            elif isinstance(result, ProviderSnapshot):
                snap = result
                # 自动补充字节数
                if snap.content and snap.bytes == 0:
                    snap.bytes = len(snap.content.encode("utf-8"))
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
        """返回预算占用、峰值、每个 provider 的指标（供 Web API 使用）。"""
        # 静态预估：所有 provider 的 max_tokens 之和
        static_estimate = sum(m.max_tokens for m in cls._providers.values())

        # 当前占用：取最近一次 collect 的结果（指定 scope 或全局）
        collect_info = cls._last_collect.get(scope, cls._last_collect.get("", {}))
        current_used = collect_info.get("used_tokens", 0)
        total_budget = collect_info.get("total_budget", 2000)

        # 峰值
        peak = cls._peak.get(scope, cls._peak.get("", 0))

        # 每个 provider 的指标
        provider_metrics = []
        for meta in cls.get_all():
            provider_metrics.append({
                "name": meta.name,
                "priority": meta.priority,
                "max_tokens": meta.max_tokens,
                "scope_filter": meta.scope_filter,
                "description": meta.description,
                "tokens": 0,
                "bytes": 0,
                "cost_ms": 0.0,
                "ready": True,
                "fetched_at": 0.0,
                "last_error": cls._last_errors.get(meta.name, ""),
                "call_count": cls._call_counts.get(meta.name, 0),
            })

        # 用最近一次 collect 的 metrics 覆盖实际值
        last_metrics = cls._last_metrics.get(scope, cls._last_metrics.get("", []))
        metrics_map = {m.name: m for m in last_metrics}
        for pm in provider_metrics:
            if pm["name"] in metrics_map:
                m = metrics_map[pm["name"]]
                pm["tokens"] = m.tokens
                pm["bytes"] = m.bytes
                pm["cost_ms"] = m.cost_ms
                pm["ready"] = m.ready
                pm["fetched_at"] = m.fetched_at

        return {
            "total_budget": total_budget,
            "static_estimate": static_estimate,
            "current_used": current_used,
            "peak_used": peak,
            "providers": provider_metrics,
        }

    # ------------------------------------------------------------------
    # 清理（测试用）
    # ------------------------------------------------------------------

    @classmethod
    def reset(cls) -> None:
        """清空所有注册（测试用）。"""
        cls._providers.clear()
        cls._call_counts.clear()
        cls._last_errors.clear()
        cls._last_metrics.clear()
        cls._last_collect.clear()
        cls._peak.clear()
