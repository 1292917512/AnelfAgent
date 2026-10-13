"""文档重排序 — 内部模型利用（llm_clients.json 的 rerank 类型模型链）。"""

from __future__ import annotations

from typing import Any, Dict, List


async def rerank_documents(query: str, documents: List[str]) -> Dict[str, Any]:
    """按 rerank 类型模型优先级链对文档列表重排序，逐模型失败回退。

    Returns:
        {"results": [...], "model": 模型名}；无可用模型/全部失败抛 RuntimeError。
    """
    from agent.llm.model_api import get_model_api

    result = await get_model_api().rerank(query, documents)
    return {"results": result.value, "model": result.model}
