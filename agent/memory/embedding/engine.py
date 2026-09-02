"""Embedding 引擎：embedding 调用的唯一入口（参考 cognee EmbeddingEngine）。

可用性状态机、端点限速、超时、重试与降级全部封装在引擎内部，
调用方只需面向两个方法，且引擎保证永不抛异常：

- embed_query: 交互式单条（召回查询/去重/技能匹配）。短超时、单次尝试、
  不占限速预算、优先于批量调用调度，结果按内容哈希短时缓存；失败返回
  None（降级 FTS-only），保证对话路径等待有上界。
- embed_text:  后台批量（回填/索引）。共享限速器保护端点，瞬时错误抖动
  重试，最终失败返回 []。

端点访问经优先级门串行调度：本地/低速端点（ollama 等）服务端本身串行，
客户端不做优先级时查询会排在批量任务之后超时；优先级门保证交互式查询
最多只等待一个进行中的调用。

故障恢复由后台 EmbeddingWorker 的 probe 探测负责；引擎处于故障状态时
所有调用直接短路，避免各路径反复冲击不可用端点。
"""

from __future__ import annotations

import asyncio
import time
from typing import Dict, Optional, Tuple

from aiolimiter import AsyncLimiter

from core.config import get_config_float, get_config_int, register_configs_safe
from core.log import log

from ..memory_utils import hash_text
from .usage import record_embedding_call

_ENGINE_CONFIGS = {
    "memory/embedding": {
        "embed_query_timeout_seconds": {
            "description": "交互式 embedding（召回查询等单条调用）超时，超时降级为 FTS-only",
            "default": 15.0,
            "advanced": True,
            "unit": "秒",
        },
        "embed_query_cache_ttl_seconds": {
            "description": "交互式查询向量的缓存时长（相同查询命中缓存不再调用端点）",
            "default": 300.0,
            "advanced": True,
            "unit": "秒",
        },
        "embed_query_cache_size": {
            "description": "交互式查询向量缓存容量（超出时淘汰最旧条目）",
            "default": 256,
            "advanced": True,
            "unit": "条",
        },
        "embed_rate_limit_requests": {
            "description": "批量限速：每个时间窗内允许的 API 请求数",
            "default": 60,
            "advanced": True,
            "unit": "次",
        },
        "embed_rate_limit_interval_seconds": {
            "description": "批量限速时间窗",
            "default": 60.0,
            "advanced": True,
            "unit": "秒",
        },
        "embed_max_retries": {
            "description": "批量失败重试次数（指数退避 + 抖动）",
            "default": 2,
            "advanced": True,
            "unit": "次",
        },
        "embedding_text_model": {
            "description": "文本域（记忆/对话/技能）使用的 embedding 模型 ID，空 = embedding 优先级首位",
            "default": "",
        },
        "embedding_vision_model": {
            "description": "视觉域（贴纸/图片索引与检索）使用的 embedding 模型 ID，空 = 跟随文本域",
            "default": "",
        },
    },
}

register_configs_safe(_ENGINE_CONFIGS)


class _PriorityGate:
    """端点访问门：同一时刻只允许一个 in-flight 调用，交互式查询优先于批量。

    低速端点服务端本身串行处理，若客户端不做调度，查询会排在批量任务
    之后超时；此门保证查询最多等待一个进行中的调用。
    """

    def __init__(self) -> None:
        self._cond = asyncio.Condition()
        self._in_flight = False
        self._waiting_queries = 0

    async def acquire(self, *, priority: bool) -> None:
        async with self._cond:
            if priority:
                self._waiting_queries += 1
            try:
                await self._cond.wait_for(
                    lambda: not self._in_flight and (priority or self._waiting_queries == 0)
                )
                self._in_flight = True
            finally:
                if priority:
                    self._waiting_queries -= 1

    async def release(self) -> None:
        async with self._cond:
            self._in_flight = False
            self._cond.notify_all()


class Embedder:
    """文本嵌入引擎，通过 LLMManager 按用途域查找 ModelType.EMBEDDING 客户端。

    purpose="text"（记忆/对话/技能）与 "vision"（贴纸/图片）各自独立实例，
    可用性状态、限速器、查询缓存互不影响。
    无可用客户端或调用失败时所有方法降级（返回 None/[]，上层回退 FTS-only）。
    """

    def __init__(self, purpose: str = "text") -> None:
        self._purpose = purpose
        self._available: Optional[bool] = None
        self._dims: Optional[int] = None
        self._limiter: Optional[AsyncLimiter] = None
        self._limiter_spec: Optional[Tuple[int, float]] = None
        self._gate = _PriorityGate()
        self._query_cache: Dict[str, Tuple[list[float], float]] = {}

    def _get_client(self):
        from agent.llm import get_llm_manager
        return get_llm_manager().get_embedding_client(self._purpose)

    @property
    def available(self) -> bool:
        if self._available is None:
            client = self._get_client()
            self._available = client is not None
            if client:
                name = getattr(client, "name", None) or getattr(getattr(client, "config", None), "name", "?")
                log(f"Embedding 客户端就绪: {name}", tag="思维")
            else:
                log("Embedding 客户端未找到，降级为 FTS-only", "WARNING", tag="思维")
        return self._available

    @property
    def dimensions(self) -> Optional[int]:
        return self._dims

    @property
    def client_name(self) -> str:
        """当前域实际使用的 embedding 模型名（无可用客户端返回空串）。"""
        client = self._get_client()
        return str(getattr(client.config, "name", "") or "") if client else ""

    @property
    def max_batch_size(self) -> int:
        """当前客户端声明的单批上限（embedding_max_batch；未配置返回 0）。"""
        client = self._get_client()
        value = getattr(getattr(client, "config", None), "embedding_max_batch", 0) if client else 0
        return int(value) if value else 0

    def invalidate(self) -> None:
        """配置变更后重新检测（含限速器与查询缓存重建）。"""
        self._available = None
        self._limiter = None
        self._limiter_spec = None
        self._query_cache.clear()

    def _ready(self) -> bool:
        """调用前置检查：故障状态短路，恢复由后台 probe 负责。"""
        return self.available and self._get_client() is not None

    def _get_limiter(self) -> AsyncLimiter:
        """后台批量共享限速器（惰性构建；配置值变化时自动重建，保存即生效）。"""
        spec = (
            max(1, get_config_int("embed_rate_limit_requests", 60)),
            max(1.0, get_config_float("embed_rate_limit_interval_seconds", 60.0)),
        )
        if self._limiter is None or self._limiter_spec != spec:
            self._limiter = AsyncLimiter(spec[0], spec[1])
            self._limiter_spec = spec
        return self._limiter

    async def _call(self, texts: list[str]) -> list[list[float]]:
        """发起一次 embedding API 调用并记录状态，失败时抛出异常由上层策略处理。"""
        client = self._get_client()
        if not client:
            raise RuntimeError("无可用 embedding 客户端")
        result = await client.embed(texts)
        record_embedding_call(texts=len(texts), chars=sum(len(t) for t in texts))
        if result and self._dims is None:
            self._dims = len(result[0])
            log(f"Embedding 维度: {self._dims}", "DEBUG", tag="思维")
        self._available = True
        return result

    # ------------------------------------------------------------------
    # 交互式（对话路径）
    # ------------------------------------------------------------------

    def _cache_get(self, key: str) -> Optional[list[float]]:
        ttl = max(0.0, get_config_float("embed_query_cache_ttl_seconds", 300.0))
        hit = self._query_cache.get(key)
        if hit and ttl > 0 and time.monotonic() - hit[1] < ttl:
            return hit[0]
        self._query_cache.pop(key, None)
        return None

    def _cache_put(self, key: str, vec: list[float]) -> None:
        capacity = max(16, get_config_int("embed_query_cache_size", 256))
        if len(self._query_cache) >= capacity:
            oldest = min(self._query_cache, key=lambda k: self._query_cache[k][1])
            self._query_cache.pop(oldest, None)
        self._query_cache[key] = (vec, time.monotonic())

    async def embed_query(self, text: str) -> Optional[list[float]]:
        """交互式单条 embedding：短超时、优先调度、结果短时缓存，失败返回 None。

        本方法只做限时降级，不改变可用性状态：超时多为与后台回填的瞬时
        争抢而非端点故障，可用性状态机由后台路径（embed_text 重试耗尽 /
        probe 探测）掌管，避免单次超时误杀后续所有轮次的向量召回。
        """
        if not text.strip() or not self._ready():
            return None
        key = hash_text(text)
        cached = self._cache_get(key)
        if cached is not None:
            return cached
        timeout = max(0.5, get_config_float("embed_query_timeout_seconds", 15.0))
        try:
            results = await asyncio.wait_for(self._gated_call([text], priority=True), timeout=timeout)
        except asyncio.TimeoutError:
            log(f"Embedding 查询超时（{timeout:.0f}s，本次降级 FTS-only）", "WARNING", tag="思维")
            return None
        except Exception as exc:
            log(f"Embedding 查询失败（本次降级 FTS-only）: {exc}", "WARNING", tag="思维")
            return None
        if not results:
            return None
        preview = text[:50].replace("\n", " ")
        log(f"Embedding 查询: \"{preview}\" → {len(results[0])}维", "DEBUG", tag="思维")
        self._cache_put(key, results[0])
        return results[0]

    @property
    def supports_vision_embedding(self) -> bool:
        """当前 embedding 客户端是否支持图片向量化（文本/图片同一向量空间）。"""
        client = self._get_client()
        return bool(client and getattr(client, "supports_multimodal_embedding", False))

    async def embed_image(self, image: str, *, text: str = "") -> Optional[list[float]]:
        """交互式图片向量化（URL 或 data URL），策略同 embed_query：限时降级、不改状态。

        图片与文本处于同一向量空间，文本查询向量可直接检索图片。
        text 非空时同请求嵌入文本与图片并做均值池化：融合向量同时保留
        视觉内容与描述语义（服务端不支持单条融合，按内容分别返回后池化）。
        当前端点不支持多模态向量时返回 None（调用方回退描述文本嵌入）。
        """
        if not image or not self._ready() or not self.supports_vision_embedding:
            return None
        key = "img:" + hash_text(f"{text}|{image}")
        cached = self._cache_get(key)
        if cached is not None:
            return cached
        contents = ([{"text": text}] if text.strip() else []) + [{"image": image}]
        timeout = max(0.5, get_config_float("embed_query_timeout_seconds", 15.0))
        try:
            results = await asyncio.wait_for(
                self._gated_call(contents, priority=True, multimodal=True),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            log(f"Embedding 图片超时（{timeout:.0f}s，本次降级）", "WARNING", tag="思维")
            return None
        except Exception as exc:
            log(f"Embedding 图片失败（本次降级）: {exc}", "WARNING", tag="思维")
            return None
        if not results:
            return None
        vec = (
            results[0]
            if len(results) == 1
            else [sum(vals) / len(results) for vals in zip(*results, strict=False)]
        )
        log(f"Embedding 图片（{'融合' if text.strip() else '纯图'}）→ {len(vec)}维", "DEBUG", tag="思维")
        self._cache_put(key, vec)
        return vec

    # ------------------------------------------------------------------
    # 后台批量（回填/索引）
    # ------------------------------------------------------------------

    async def embed_text(self, texts: list[str]) -> list[list[float]]:
        """后台批量 embedding：共享限速 + 优先级门让行 + 抖动重试，最终失败返回 []。"""
        # 空文本会导致部分 embedding API 整批 400，以占位符兜底
        texts = [t if t.strip() else " " for t in texts]
        if not texts or not self._ready():
            return []
        attempts = 1 + max(0, get_config_int("embed_max_retries", 2))
        from agent.llm.retry import jittered_backoff
        for attempt in range(1, attempts + 1):
            try:
                async with self._get_limiter():
                    result = await self._gated_call(texts, priority=False)
                log(f"Embedding 批量: {len(texts)} 条文本 → {len(result)} 个向量", "DEBUG", tag="思维")
                return result
            except Exception as exc:
                reason = f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__
                if attempt >= attempts:
                    log(f"Embedding 批量失败（降级 FTS-only）: {reason}", "WARNING", tag="思维")
                    self._available = False
                    return []
                delay = jittered_backoff(attempt, base_delay=1.0, max_delay=10.0)
                log(f"Embedding 批量第 {attempt} 次失败，{delay:.1f}s 后重试: {reason}", "DEBUG", tag="思维")
                await asyncio.sleep(delay)
        return []

    async def _call_multimodal(self, contents: list[dict]) -> list[list[float]]:
        """多模态向量调用（text/image contents），失败抛出由上层策略处理。"""
        client = self._get_client()
        if not client:
            raise RuntimeError("无可用 embedding 客户端")
        result = await client.embed_multimodal(contents)
        record_embedding_call(
            texts=len(contents),
            chars=sum(len(str(c.get("text", ""))) for c in contents),
        )
        if result and self._dims is None:
            self._dims = len(result[0])
            log(f"Embedding 维度: {self._dims}", "DEBUG", tag="思维")
        self._available = True
        return result

    async def _gated_call(
        self,
        payload: list,
        *,
        priority: bool,
        multimodal: bool = False,
    ) -> list[list[float]]:
        """经优先级门调度后执行 API 调用（查询优先于批量，单飞串行）。"""
        await self._gate.acquire(priority=priority)
        try:
            if multimodal:
                return await self._call_multimodal(payload)
            return await self._call(payload)
        finally:
            await self._gate.release()

    async def probe(self) -> bool:
        """故障恢复探测（后台 worker 调用）：单条试嵌，成功则恢复可用状态。"""
        client = self._get_client()
        if not client:
            self._available = False
            return False
        try:
            result = await client.embed(["ping"])
            self._available = bool(result)
            if result:
                self._dims = len(result[0])
            return self._available
        except Exception:
            self._available = False
            return False


# ----------------------------------------------------------------------
# 分域实例工厂
# ----------------------------------------------------------------------

_embedders: Dict[str, Embedder] = {}


def get_embedder(purpose: str = "text") -> Embedder:
    """按用途域获取共享 Embedder 实例（text=文本域 / vision=视觉域）。"""
    if purpose not in ("text", "vision"):
        purpose = "text"
    embedder = _embedders.get(purpose)
    if embedder is None:
        embedder = Embedder(purpose=purpose)
        _embedders[purpose] = embedder
    return embedder


def invalidate_embedders() -> None:
    """模型/配置变更后重置全部域实例（下次调用重新检测）。"""
    for embedder in _embedders.values():
        embedder.invalidate()
