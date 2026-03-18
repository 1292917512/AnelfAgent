"""Embedder：通过 LLMManager 自动查找 embedding 类型的客户端。"""

from __future__ import annotations

from typing import Optional

from core.log import log


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

    async def embed(self, texts: list[str]) -> list[list[float]]:
        client = self._get_client()
        if not client:
            log("Embedding 跳过: 无可用客户端", "DEBUG", tag="思维")
            return []
        try:
            result = await client.embed(texts)
            if result and self._dims is None:
                self._dims = len(result[0])
                log(f"Embedding 维度: {self._dims}", "DEBUG", tag="思维")
            self._available = True
            log(f"Embedding 完成: {len(texts)} 条文本 → {len(result)} 个向量", "DEBUG", tag="思维")
            return result
        except Exception as exc:
            log(f"Embedding 调用失败，降级为 FTS-only: {exc}", "WARNING", tag="思维")
            self._available = False
            return []

    async def embed_one(self, text: str) -> Optional[list[float]]:
        preview = text[:50].replace("\n", " ")
        results = await self.embed([text])
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
