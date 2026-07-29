"""Embedding 引擎（Embedder）行为测试：超时/降级/限速/重试/恢复探测。"""

from __future__ import annotations

import asyncio
import time

from aiolimiter import AsyncLimiter

from agent.memory.embedding import Embedder
from agent.memory.embedding import engine as engine_module


class FakeClient:
    """可控 embedding 客户端 stub。"""

    def __init__(self, dims: int = 4, delay: float = 0.0) -> None:
        self.dims = dims
        self.delay = delay
        self.calls: list[list[str]] = []

    async def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        if self.delay:
            await asyncio.sleep(self.delay)
        return [[1.0] + [0.0] * (self.dims - 1) for _ in texts]


class FlakyClient(FakeClient):
    """前 fail_times 次调用抛异常，之后恢复正常。"""

    def __init__(self, fail_times: int, **kwargs) -> None:
        super().__init__(**kwargs)
        self.fail_times = fail_times

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if self.fail_times > 0:
            self.fail_times -= 1
            raise RuntimeError("transient")
        return await super().embed(texts)


def _make_embedder(client: FakeClient) -> Embedder:
    e = Embedder()
    e._get_client = lambda: client  # type: ignore[method-assign]
    return e


class TestEmbedQuery:
    async def test_returns_vector(self) -> None:
        e = _make_embedder(FakeClient())
        vec = await e.embed_query("查询文本")
        assert vec is not None
        assert len(vec) == 4
        assert e.available is True

    async def test_timeout_degrades_without_killing_state(self, monkeypatch) -> None:
        """慢端点：embed_query 超时返回 None，但不改变可用性状态。

        超时多为与后台回填的瞬时争抢（端点其实存活），可用性状态机由
        后台路径掌管，避免单次超时误杀后续所有轮次的向量召回。
        """
        monkeypatch.setattr(
            engine_module, "get_config_float", lambda key, default=0.0: 0.05
        )
        e = _make_embedder(FakeClient(delay=60.0))
        start = time.monotonic()
        vec = await e.embed_query("查询文本")
        assert vec is None
        assert time.monotonic() - start < 5.0
        assert e.available is True

    async def test_empty_text_short_circuits(self) -> None:
        client = FakeClient()
        e = _make_embedder(client)
        assert await e.embed_query("   ") is None
        assert client.calls == []

    async def test_unavailable_short_circuits(self) -> None:
        """故障状态下不再冲击端点（恢复由 probe 负责）。"""
        client = FakeClient()
        e = _make_embedder(client)
        e._available = False
        assert await e.embed_query("查询文本") is None
        assert client.calls == []

    async def test_exception_keeps_state(self) -> None:
        """查询失败仅本次降级，可用性状态由后台路径（embed_text/probe）掌管。"""
        e = _make_embedder(FlakyClient(fail_times=99))
        assert await e.embed_query("x") is None
        assert e.available is True


class TestEmbedText:
    async def test_batch_success(self) -> None:
        e = _make_embedder(FakeClient(delay=0.05))
        result = await e.embed_text(["a", "b", "c"])
        assert len(result) == 3
        assert e.available is True

    async def test_empty_text_sanitized(self) -> None:
        """空白文本以占位符兜底，避免部分 API 整批 400。"""
        client = FakeClient()
        e = _make_embedder(client)
        result = await e.embed_text(["正常", "  "])
        assert len(result) == 2
        assert client.calls[0][1] == " "

    async def test_retry_then_success(self, monkeypatch) -> None:
        """瞬时失败按抖动退避重试，后续尝试成功则整体成功。"""
        monkeypatch.setattr("agent.llm.retry.jittered_backoff", lambda *a, **k: 0.01)
        e = _make_embedder(FlakyClient(fail_times=1))
        result = await e.embed_text(["a"])
        assert len(result) == 1
        assert e.available is True

    async def test_retries_exhausted_degrades(self, monkeypatch) -> None:
        monkeypatch.setattr("agent.llm.retry.jittered_backoff", lambda *a, **k: 0.01)
        e = _make_embedder(FlakyClient(fail_times=99))
        assert await e.embed_text(["a"]) == []
        assert e.available is False

    async def test_uses_shared_rate_limiter(self, monkeypatch) -> None:
        """后台批量路径经共享限速器（配置在首次使用时读取）。"""
        monkeypatch.setattr(
            engine_module, "get_config_int",
            lambda key, default=0: 7 if key == "embed_rate_limit_requests" else default,
        )
        e = _make_embedder(FakeClient())
        assert e._limiter is None
        await e.embed_text(["a"])
        assert isinstance(e._limiter, AsyncLimiter)
        assert e._limiter.max_rate == 7

    async def test_query_path_bypasses_limiter(self) -> None:
        """交互式路径不占限速预算（不创建/不经过限速器）。"""
        e = _make_embedder(FakeClient())
        await e.embed_query("查询文本")
        assert e._limiter is None


class TestQueryCache:
    async def test_repeated_query_hits_cache(self) -> None:
        """相同查询在 TTL 内命中缓存，不再调用端点。"""
        client = FakeClient()
        e = _make_embedder(client)
        v1 = await e.embed_query("相同查询")
        v2 = await e.embed_query("相同查询")
        assert v1 is not None and v1 == v2
        assert len(client.calls) == 1

    async def test_cache_not_shared_across_texts(self) -> None:
        client = FakeClient()
        e = _make_embedder(client)
        await e.embed_query("查询一")
        await e.embed_query("查询二")
        assert len(client.calls) == 2


class TestPriorityGate:
    async def test_query_preempts_queued_batch(self) -> None:
        """批量占用端点时，交互式查询优先于排队中的批量获得调度。"""
        client = FakeClient(delay=0.1)
        e = _make_embedder(client)

        batch1 = asyncio.create_task(e.embed_text(["batch1"]))
        await asyncio.sleep(0.03)  # 确保 batch1 已占用端点
        batch2 = asyncio.create_task(e.embed_text(["batch2"]))
        await asyncio.sleep(0.01)  # batch2 先于查询排队
        query = asyncio.create_task(e.embed_query("q"))

        await asyncio.gather(batch1, batch2, query)
        order = [c[0] for c in client.calls]
        assert order == ["batch1", "q", "batch2"]

    async def test_gate_released_after_query_timeout(self, monkeypatch) -> None:
        """查询超时后门正确释放，后续批量调用不受影响。"""
        monkeypatch.setattr(
            engine_module, "get_config_float", lambda key, default=0.0: 0.05
        )
        e = _make_embedder(FakeClient(delay=60.0))
        assert await e.embed_query("查询文本") is None
        # 门未泄漏：批量调用可正常获得调度
        e._get_client = lambda: FakeClient()  # type: ignore[method-assign]
        result = await e.embed_text(["a"])
        assert len(result) == 1


class TestProbe:
    async def test_probe_recovers(self) -> None:
        e = _make_embedder(FakeClient())
        e._available = False
        assert await e.probe() is True
        assert e.available is True
        assert e.dimensions == 4

    async def test_probe_failure_stays_unavailable(self) -> None:
        e = _make_embedder(FlakyClient(fail_times=99))
        assert await e.probe() is False
        assert e.available is False

    async def test_invalidate_resets_state(self) -> None:
        e = _make_embedder(FakeClient())
        await e.embed_text(["a"])
        e.invalidate()
        assert e._available is None
        assert e._limiter is None


class TestDomainFactory:
    def test_get_embedder_per_purpose(self) -> None:
        from agent.memory.embedding import get_embedder
        text = get_embedder("text")
        vision = get_embedder("vision")
        assert text is get_embedder("text")
        assert vision is get_embedder("vision")
        assert text is not vision
        # 未知用途归一为 text 域
        assert get_embedder("unknown") is text
