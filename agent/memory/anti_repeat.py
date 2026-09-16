"""防复读：近期回复的话题新鲜度纪律（软提示）+ 主动任务发送闸门。

两级防线，作用面刻意不同：
- 软提示（build_repeat_hint → context 管线 freshness 块）：统计当前会话
  最近 N 条 AI 回复里反复出现的话题 ngram，注入提示引导生成侧换角度。
  覆盖全部回复周期——包括提醒/任务触发的 REPLY（提醒必须送达，只能在
  生成侧引导，不能拦）；
- 硬闸门（check_repeat_gate → execute_send_action）：仅对 ``reflect:```
  任务上下文的主动发送（心跳任务/主动搭话的 send_message）生效——草稿
  与近期 AI 回复高度重叠时拒发，防主动消息复读刷屏。用户触发的正常
  回复不设闸：用户问什么答什么，重复是用户的选择。

评分用字符 2-gram Dice（中英通用、无分词依赖）：overlap = 2|A∩B|/(|A|+|B|)。
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

from core.config import (
    get_config_bool,
    get_config_float,
    get_config_int,
    register_configs_safe,
)
from core.log import log

_REPEAT_CONFIGS = {
    "memory/anti_repeat": {
        "memory_repeat_hint_enabled": {
            "description": "是否注入话题新鲜度提示（最近回复的高频话题提醒）",
            "default": True,
        },
        "memory_repeat_hint_top_k": {
            "description": "软提示最多列出的话题词数",
            "default": 5,
            "advanced": True,
            "unit": "个",
        },
        "memory_repeat_hint_min_df": {
            "description": "话题词需在至少多少条近期回复中出现过",
            "default": 3,
            "advanced": True,
            "unit": "条",
        },
        "memory_repeat_window": {
            "description": "统计/比对的近期 AI 回复条数",
            "default": 20,
            "advanced": True,
            "unit": "条",
        },
        "memory_repeat_gate_enabled": {
            "description": "是否启用主动任务发送的复读闸门（reflect: 上下文）",
            "default": True,
        },
        "memory_repeat_gate_threshold": {
            "description": "复读闸门重叠率阈值（0~1，超过则拒发）",
            "default": 0.55,
            "advanced": True,
            "value_type": "range",
            "min": 0,
            "max": 1,
            "step": 0.05,
        },
        "memory_repeat_min_chars": {
            "description": "短消息不参与闸门评分（低于该字符数直接放行）",
            "default": 24,
            "advanced": True,
            "unit": "字符",
        },
    },
}

register_configs_safe(_REPEAT_CONFIGS)

# 闸门比对的前景窗口（只与最近几条比：更早的重复属"话题回顾"而非复读）
_GATE_WINDOW = 5

_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_WORD_RE = re.compile(r"[a-z0-9]{2,}")


def tokenize(text: str) -> set[str]:
    """话题 token 集：中文 2-gram + 拉丁/数字词（小写）。"""
    tokens: set[str] = set()
    if not text:
        return tokens
    lowered = text.lower()
    if _CJK_RE.search(lowered):
        compact = re.sub(r"\s+", "", lowered)
        tokens.update(compact[i:i + 2] for i in range(len(compact) - 1))
    tokens.update(_WORD_RE.findall(lowered))
    return tokens


def compute_hint_terms(
    recent_texts: List[str], *, top_k: int = 0, min_df: int = 0,
) -> List[str]:
    """从近期 AI 回复中统计反复出现的话题词（df ≥ min_df，按频次/长度排序）。"""
    if not top_k:
        top_k = max(1, get_config_int("memory_repeat_hint_top_k", 5))
    if not min_df:
        min_df = max(2, get_config_int("memory_repeat_hint_min_df", 3))
    if len(recent_texts) < min_df:
        return []
    df: Dict[str, int] = {}
    for text in recent_texts:
        for token in tokenize(text):
            df[token] = df.get(token, 0) + 1
    repeated = [t for t, n in df.items() if n >= min_df and len(t) >= 2]
    repeated.sort(key=lambda t: (-df[t], -len(t)))
    return repeated[:top_k]


def overlap_ratio(a: set[str], b: set[str]) -> float:
    """Dice 重叠率：2|A∩B|/(|A|+|B|)，空集返回 0。"""
    if not a or not b:
        return 0.0
    return 2.0 * len(a & b) / (len(a) + len(b))


def repeat_score(draft: str, recent_texts: List[str]) -> float:
    """草稿与前景窗口内最近回复的最大重叠率（0~1）。"""
    draft_tokens = tokenize(draft)
    if not draft_tokens:
        return 0.0
    return max(
        (overlap_ratio(draft_tokens, tokenize(t)) for t in recent_texts[-_GATE_WINDOW:]),
        default=0.0,
    )


# ── 会话助手 ─────────────────────────────────────────────────────────

async def _recent_assistant_texts(scope: str) -> Tuple[List[str], Optional[int]]:
    """取 scope 最近 N 条 assistant 消息文本与最新一条的消息 id（缓存锚点）。

    scope 为会话 entity scope（user_xxx:yyy / group_xxx:yyy）；解析失败或
    runtime 未就绪返回 ([], None)。
    """
    if not scope:
        return [], None
    try:
        from agent.messages import parse_entity_scope
        from agent.runtime.singleton import require_runtime
        sqlite = require_runtime().data_center.sqlite
        window = max(5, get_config_int("memory_repeat_window", 20))
        scope_type, adapter, base_id, session_id = parse_entity_scope(scope)
        scope_id = f"{adapter}:{base_id}" + (f"#{session_id}" if session_id else "")
        rows = await sqlite.fetch_conversation(
            scope_type=scope_type, scope_id=scope_id, limit=window * 3,
        )
        assistant = [r for r in rows if r.get("role") == "assistant"]
        assistant = assistant[-window:]
        texts = [str(r.get("content", "")) for r in assistant]
        last_id = int(assistant[-1]["id"]) if assistant else None
        return texts, last_id
    except Exception as exc:
        log(f"近期回复读取失败 [{scope}]: {exc}", "DEBUG", tag="记忆")
        return [], None


# 提示缓存：{scope: (最新 assistant 消息 id, 上次提示文本)}——消息 id 不变不重算
_HINT_CACHE_MAX_SCOPES = 64
_hint_cache: Dict[str, Tuple[Optional[int], str]] = {}


async def build_repeat_hint(scope: str) -> str:
    """渲染 freshness 注入块；近期无反复话题返回空串（块不注入）。"""
    if not scope or not get_config_bool("memory_repeat_hint_enabled", True):
        return ""
    texts, last_id = await _recent_assistant_texts(scope)
    cached = _hint_cache.get(scope)
    if cached is not None and cached[0] == last_id:
        return cached[1]
    terms = compute_hint_terms(texts)
    hint = (
        "[系统注入·话题新鲜度] 最近几条回复已反复谈及："
        + "、".join(terms)
        + "。本次回复注意换新角度或新话题，除非对方主动回到这些话题。"
        if terms else ""
    )
    if len(_hint_cache) >= _HINT_CACHE_MAX_SCOPES and scope not in _hint_cache:
        _hint_cache.pop(next(iter(_hint_cache)), None)
    _hint_cache[scope] = (last_id, hint)
    return hint


async def check_repeat_gate(scope: str, draft: str) -> Optional[str]:
    """主动任务发送闸门：高度复读返回拒绝 JSON，放行返回 None。

    仅应在 reflect: 任务上下文的发送路径调用（execute_send_action 内，
    与 outbound_guard 同一判定前缀）——用户触发的回复周期不经此闸。
    """
    if not scope or not draft or not get_config_bool("memory_repeat_gate_enabled", True):
        return None
    min_chars = max(0, get_config_int("memory_repeat_min_chars", 24))
    if len(draft) < min_chars:
        return None
    texts, _ = await _recent_assistant_texts(scope)
    if not texts:
        return None
    score = repeat_score(draft, texts)
    threshold = min(1.0, max(0.0, get_config_float("memory_repeat_gate_threshold", 0.55)))
    if score < threshold:
        return None
    import json

    from entities._sdk import ErrorCause
    log(f"复读闸门拦截 [{scope}]: 重叠率 {score:.2f} ≥ {threshold:.2f}", tag="记忆")
    return json.dumps({
        "success": False,
        "error": f"本条主动消息与最近回复高度重复（重叠率 {score:.2f}）",
        "cause": ErrorCause.STATE.value,
        "guard": "repeat",
        "retryable": False,
        "hint": "换一个角度或话题重新组织内容；若事项确需提醒，精简为一句关键信息",
    }, ensure_ascii=False)
