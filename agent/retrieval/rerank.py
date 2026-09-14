"""文档重排序 — 内部模型利用（llm_clients.json 的 rerank 类型模型链）。"""

from __future__ import annotations

from typing import Any, Dict, List

from core.log import log

_LOG_TAG = "检索"


async def rerank_documents(query: str, documents: List[str]) -> Dict[str, Any]:
    """按 rerank 类型模型优先级链对文档列表重排序，逐模型失败回退。

    Returns:
        {"results": [...], "model": 模型名}；无可用模型/全部失败抛 RuntimeError。
    """
    from agent.llm import get_llm_manager
    pairs = get_llm_manager().iter_media_for_type("rerank")
    if not pairs:
        raise RuntimeError("未配置 rerank 类型模型")
    errors: Dict[str, str] = {}
    for model_name, client in pairs:
        try:
            return {"results": await client.rerank(query, documents, model=model_name),
                    "model": model_name}
        except Exception as exc:
            detail = str(exc).strip() or type(exc).__name__
            errors[model_name] = detail[:200]
            log(f"rerank 模型 {model_name} 调用失败，尝试下一个: {detail}",
                "WARNING", tag=_LOG_TAG)
            continue
    raise RuntimeError(f"所有 rerank 模型均调用失败: {'; '.join(f'{k}: {v}' for k, v in errors.items())}")
