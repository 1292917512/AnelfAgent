"""统一异常摘要，不依赖 SDK 类型或业务协调器。"""

from __future__ import annotations


def error_text(exc: BaseException) -> str:
    """生成可见的错误描述；asyncio.TimeoutError 等异常的 str() 为空。"""
    name = type(exc).__name__
    cause = getattr(exc, "error_class", None)
    if cause and hasattr(exc, "error_message"):
        message = getattr(exc, "error_message", None) or "底层未提供错误详情"
        dataset = getattr(exc, "dataset_name", None)
        return f"{name}: {cause}: {message}" + (f"（数据集: {dataset}）" if dataset else "")
    text = str(exc).strip()
    if not text:
        return name
    return text if text.startswith(f"{name}:") else f"{name}: {text}"
