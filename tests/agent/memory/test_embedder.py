"""Embedder 交互式超时保护与降级行为测试。"""

from __future__ import annotations

import asyncio
import time

from agent.memory import embedder as embedder_module
from agent.memory.embedder import Embedder


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


def _make_embedder(client: FakeClient) -> Embedder:
    e = Embedder()
    e._get_client = lambda: client  # type: ignore[method-assign]
    return e


class TestEmbedTimeout:
    async def test_embed_one_returns_vector(self) -> None:
        e = _make_embedder(FakeClient())
        vec = await e.embed_one("查询文本")
        assert vec is not None
        assert len(vec) == 4
        assert e.available is True

    async def test_embed_one_timeout_degrades(self) -> None:
        """慢端点：embed_one 在超时内返回 None 并标记不可用（降级 FTS-only）。"""
        e = _make_embedder(FakeClient(delay=60.0))
        start = time.monotonic()
        vec = await e.embed_one("查询文本", timeout=0.05)
        elapsed = time.monotonic() - start
        assert vec is None
        assert elapsed < 5.0
        assert e.available is False

    async def test_embed_one_default_timeout_from_config(self, monkeypatch) -> None:
        """未显式传 timeout 时，默认套用 embed_query_timeout_seconds 配置。"""
        monkeypatch.setattr(
            embedder_module, "get_config_float", lambda key, default=0.0: 0.05
        )
        e = _make_embedder(FakeClient(delay=60.0))
        start = time.monotonic()
        vec = await e.embed_one("查询文本")
        assert vec is None
        assert time.monotonic() - start < 5.0

    async def test_batch_embed_without_timeout_unaffected(self) -> None:
        """批量回填走 embed() 且默认不限时，慢调用也可正常完成。"""
        e = _make_embedder(FakeClient(delay=0.05))
        result = await e.embed(["a", "b", "c"])
        assert len(result) == 3
        assert e.available is True

    async def test_embed_exception_marks_unavailable(self) -> None:
        class FailClient(FakeClient):
            async def embed(self, texts: list[str]) -> list[list[float]]:
                raise RuntimeError("boom")

        e = _make_embedder(FailClient())
        assert await e.embed_one("x", timeout=1.0) is None
        assert e.available is False
