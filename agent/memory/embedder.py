"""Embedder：通过 LLMManager 自动查找 embedding 类型的客户端。"""

from __future__ import annotations

import asyncio
from typing import Optional

from core.config import get_config_float, register_configs_safe
from core.log import log

_EMBEDDER_CONFIGS = {
    "记忆": {
        "embed_query_timeout_seconds": {
            "description": "交互式 embedding（召回查询等单条调用）的超时时间（秒），超时降级为 FTS-only",
            "default": 5.0,
        },
    },
}

register_configs_safe(_EMBEDDER_CONFIGS)


class Embedder:
    """将文本转换为嵌入向量。

    通过 LLMManager 查找 ModelType.EMBEDDING 类型的客户端。
    无可用客户端时所有调用返回空列表（降级为 FTS-only）。
    """

    def __init__(self) -> None:
        self._available: Optional[bool] = None
        self._dims: Optional[int] = None

    def _get_client(self):
        from agent.llm import get_llm_manager
        return get_llm_manager().get_embedding_client()

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

    def invalidate(self) -> None:
        """配置变更后重新检测。"""
        self._available = None

    async def embed(self, texts: list[str], *, timeout: Optional[float] = None) -> list[list[float]]:
        client = self._get_client()
        if not client:
            log("Embedding 跳过: 无可用客户端", "DEBUG", tag="思维")
            return []
        try:
            if timeout is not None:
                result = await asyncio.wait_for(client.embed(texts), timeout=timeout)
            else:
                result = await client.embed(texts)
            if result and self._dims is None:
                self._dims = len(result[0])
                log(f"Embedding 维度: {self._dims}", "DEBUG", tag="思维")
            self._available = True
            log(f"Embedding 完成: {len(texts)} 条文本 → {len(result)} 个向量", "DEBUG", tag="思维")
            return result
        except asyncio.TimeoutError:
            # 交互式调用的延迟守卫：超时同样降级为 FTS-only（由后台 probe 探测恢复），
            # 避免 embedding 端点拥塞时阻塞对话路径
            log(f"Embedding 调用超时（{timeout}s），降级为 FTS-only", "WARNING", tag="思维")
            self._available = False
            return []
        except Exception as exc:
            log(f"Embedding 调用失败，降级为 FTS-only: {exc}", "WARNING", tag="思维")
            self._available = False
            return []

    async def embed_one(self, text: str, *, timeout: Optional[float] = None) -> Optional[list[float]]:
        preview = text[:50].replace("\n", " ")
        # embed_one 只用于交互式路径（召回查询/去重/技能匹配），默认套用查询超时，
        # 保证对话路径的 embedding 等待有上界；批量回填走 embed() 不受此限
        effective_timeout = timeout if timeout is not None else max(
            0.5, get_config_float("embed_query_timeout_seconds", 5.0)
        )
        results = await self.embed([text], timeout=effective_timeout)
        if results:
            log(f"Embedding 单条: \"{preview}\" → {len(results[0])}维", "DEBUG", tag="思维")
        else:
            log(f"Embedding 单条失败: \"{preview}\"", "DEBUG", tag="思维")
        return results[0] if results else None

    async def probe(self) -> bool:
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
