"""Token 计数与模型信息工具：litellm tokenizer / 模型元数据的薄封装（含兜底）。"""

from __future__ import annotations

import os
from typing import Any, Dict, Optional

# 必须在 import litellm 之前设置，阻止启动时拉取远端模型价格表
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

import litellm

from core.log import debug


@staticmethod
def count_tokens(model: str, messages: list[dict]) -> int:
    """计算消息列表的 token 数（基于模型的 tokenizer）。

    tokenizer 不可用时返回字符数/4 的兜底估计并记录 DEBUG 日志，
    避免静默返回 0 导致上游误判上下文为空。
    """
    try:
        return litellm.token_counter(model=model, messages=messages)
    except Exception as exc:
        total = 0
        for msg in messages:
            content = msg.get("content")
            if isinstance(content, str):
                total += len(content)
            elif isinstance(content, list):
                total += sum(
                    len(part["text"])
                    for part in content
                    if isinstance(part, dict) and isinstance(part.get("text"), str)
                )
        debug(
            f"token_counter 失败 ({type(exc).__name__}: {exc})，"
            f"使用字符数/4 兜底估计 ({total} 字符)",
            tag="模型",
        )
        return total // 4

@staticmethod
def count_text_tokens(model: str, text: str) -> int:
    """计算纯文本的 token 数（失败时字符数/4 兜底估计）。"""
    try:
        return litellm.token_counter(model=model, text=text)
    except Exception as exc:
        debug(
            f"token_counter 失败 ({type(exc).__name__}: {exc})，"
            f"使用字符数/4 兜底估计 ({len(text)} 字符)",
            tag="模型",
        )
        return len(text) // 4

@staticmethod
def get_max_tokens(model: str) -> Optional[int]:
    """查询模型的最大上下文 token 数。"""
    try:
        return litellm.get_max_tokens(model)
    except Exception:
        return None

@staticmethod
def get_model_info(model: str) -> Dict[str, Any]:
    """查询模型完整信息（上下文窗口 / 输出上限 / 能力 / 价格）。"""
    try:
        return litellm.get_model_info(model)
    except Exception:
        return {}

@staticmethod
def get_model_cost(model: str) -> Optional[Dict[str, Any]]:
    """查询模型的价格信息（input_cost_per_token / output_cost_per_token 等）。"""
    return litellm.model_cost.get(model)
