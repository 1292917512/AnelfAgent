"""LLM 端点 URL 智能归一化。

用户填写的 base_url 形态各异：可能只填到域名、填到 /v1、填到 /v4
（Z.AI 等非 v1 版本段），甚至直接粘贴包含端点路径的完整请求地址
（.../v1/chat/completions、.../v1/responses、.../v1/messages）。
本模块把这些形态统一归一：

- split_endpoint_suffix: 剥离尾部已知端点路径，返回 (api_base, 协议形态)
- infer_chat_protocol:   按 URL 末段推断对话协议（URL 是端点的事实真相）
- models_endpoint_candidates: 模型列表端点候选（先 /models 后 /v1/models 回退）
- join_endpoint:         防双拼的路径拼接
"""

from __future__ import annotations

import re
from typing import List, Optional, Tuple

# 版本段（/v1、/v4、/v1beta 等）：用于 base 与 path 的版本前缀去重
_VERSION_TAIL_RE = re.compile(r"/v\d+[a-z0-9]*$", re.IGNORECASE)
_VERSION_HEAD_RE = re.compile(r"^/v\d+[a-z0-9]*", re.IGNORECASE)

# 已知端点路径后缀 → 协议形态。
# 顺序即匹配优先级：/v1/chat/completions 先于 /chat/completions 无意义，
# 统一按字符串后缀匹配即可（/v1/chat/completions 本身以 /chat/completions 结尾）。
_ENDPOINT_SUFFIXES: Tuple[Tuple[str, str], ...] = (
    ("/chat/completions", "chat_completions"),
    ("/responses", "responses"),
    ("/messages", "messages"),
)


def split_endpoint_suffix(base_url: str) -> Tuple[str, Optional[str]]:
    """剥离 base_url 尾部的已知端点路径。

    返回 (api_base, shape)：
    - shape 为 "chat_completions" / "responses" / "messages" / None
    - api_base 为剥离端点路径后的地址（不含尾部斜杠），可直接交给
      litellm 等客户端库自行拼接端点，避免双拼。

    例：
        https://a.com/v1/chat/completions → (https://a.com/v1, chat_completions)
        https://a.com/v4                  → (https://a.com/v4, None)
    """
    url = (base_url or "").strip().rstrip("/")
    if not url:
        return "", None
    lower = url.lower()
    for suffix, shape in _ENDPOINT_SUFFIXES:
        if lower.endswith(suffix):
            return url[: -len(suffix)], shape
    return url, None


def infer_chat_protocol(base_url: str) -> Optional[str]:
    """按 URL 末段推断对话协议。

    仅当 URL 显式携带端点路径时返回协议名（此时 URL 是端点形态的
    事实真相，优先级高于配置推断）；否则返回 None 交由配置决定。
    Anthropic 的 /messages 不参与 chat/responses 推断（由 api_type 决定）。
    """
    _, shape = split_endpoint_suffix(base_url)
    if shape in ("chat_completions", "responses"):
        return shape
    return None


def join_endpoint(base_url: str, path: str) -> str:
    """防双拼的端点拼接：base 已含该端点（或其末段）时直接用 base。

    例：
        join_endpoint("https://a.com/v1", "/v1/messages")      → https://a.com/v1/messages
        join_endpoint("https://a.com/v1/messages", "/v1/messages") → https://a.com/v1/messages
        join_endpoint("https://a.com/messages", "/v1/messages")    → https://a.com/messages
        join_endpoint("https://a.com/v4", "/v1/chat/completions")  → https://a.com/v4/chat/completions
    """
    base = (base_url or "").strip().rstrip("/")
    path = "/" + path.strip().lstrip("/")
    if not base:
        return path
    lower = base.lower()
    if lower.endswith(path.lower()):
        return base
    # base 末段与 path 末段一致（如 base=.../messages, path=/v1/messages）
    tail = path.rsplit("/", 1)[-1]
    if tail and lower.endswith("/" + tail.lower()):
        return base
    # base 以 /vN 结尾且 path 自带版本前缀：剥离 path 的版本段防双版本
    # （覆盖 Z.AI /v4 这类非 v1 渠道）
    if _VERSION_TAIL_RE.search(lower):
        stripped = _VERSION_HEAD_RE.sub("", path)
        if stripped and stripped != path:
            return base + stripped
    return base + path


def models_endpoint_candidates(base_url: str) -> List[str]:
    """模型列表端点候选地址（按优先级去重）。

    策略：
    1. 剥离尾部已知端点路径（/chat/completions、/responses、/messages）；
    2. 末段已是 /models → 直接使用；
    3. 否则先试 <base>/models，再回退 <base>/v1/models
       （用户填到域名或未带版本段时，兼容端点多挂在 /v1 下）。
    """
    api_base, _ = split_endpoint_suffix(base_url)
    if not api_base:
        return []
    if api_base.lower().endswith("/models"):
        return [api_base]
    candidates = [f"{api_base}/models"]
    fallback = f"{api_base}/v1/models"
    if fallback not in candidates:
        candidates.append(fallback)
    return candidates
