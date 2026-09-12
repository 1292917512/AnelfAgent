"""上下文快照构造与冻结。

把「触发时刻的 LLM 上下文」固化为可被异步钩子安全消费的输入：
- 冻结：浅拷贝列表 + 逐条 dict 浅拷贝，切断与主对话消息的引用共享，
  主对话后续 mutate（tool_chain 追加 / content 改写）不会污染快照；
- 规整：发送边界经 normalize_for_send 剥离 _layer/_source 内部分类标签，
  并做配对修复/角色归一/尾部 prefill 修复——与主对话发送口径一致。

transcript 档位有字符护栏：超上限时保头保尾截断并标记，防止异常巨型
上下文撑爆钩子的 reflect 预算。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from agent.mind.message_schema import normalize_for_send

# transcript 快照的字符护栏默认上限（保头 70% + 保尾 30%，中间计条省略）；
# 生效值取配置 hooks_llm_transcript_max_chars
_SNAPSHOT_MAX_CHARS = 60_000
_HEAD_FRACTION = 0.7


def _snapshot_max_chars() -> int:
    """transcript 快照字符护栏（配置热读，异常回退默认）。"""
    try:
        from core.config import get_config_int
        return max(1000, get_config_int("hooks_llm_transcript_max_chars", _SNAPSHOT_MAX_CHARS))
    except Exception:
        return _SNAPSHOT_MAX_CHARS


def freeze_messages(messages: Optional[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    """冻结消息链为快照副本（浅拷贝列表 + 逐条 dict 浅拷贝）。

    非 dict 条目原样丢弃；空/None 输入返回空列表。浅拷贝已足够——
    主对话对消息链的后续操作是「追加新 dict」而非原地改 content，
    dict 浅拷贝切断的是列表与外层 dict 的引用，内部不可变字符串安全共享。
    """
    if not messages:
        return []
    return [dict(m) for m in messages if isinstance(m, dict)]


def cap_snapshot_chars(messages: List[Dict[str, Any]],
                       max_chars: int = 0) -> List[Dict[str, Any]]:
    """按字符预算护栏截断快照（保头保尾 + 省略标记）。

    以各消息 content 字符数累计；超限时保留头部 70% 与尾部 30%，
    在中间插入一条 system 省略标记，保证钩子仍能看到首尾语境。
    max_chars<=0 时取配置 hooks_llm_transcript_max_chars。
    """
    if max_chars <= 0:
        max_chars = _snapshot_max_chars()
    total = sum(len(str(m.get("content") or "")) for m in messages)
    if total <= max_chars:
        return messages
    head_budget = int(max_chars * _HEAD_FRACTION)
    tail_budget = max_chars - head_budget

    head: List[Dict[str, Any]] = []
    used = 0
    for m in messages:
        c = len(str(m.get("content") or ""))
        if used + c > head_budget:
            break
        head.append(m)
        used += c

    tail: List[Dict[str, Any]] = []
    used = 0
    for m in reversed(messages):
        c = len(str(m.get("content") or ""))
        if used + c > tail_budget:
            break
        tail.append(m)
        used += c
    tail.reverse()

    omitted = len(messages) - len(head) - len(tail)
    marker = {
        "role": "system",
        "content": f"[上下文快照] 原始共 {len(messages)} 条 / {total} 字符，"
                   f"中间 {omitted} 条已省略（保头保尾截断）。",
    }
    return [*head, marker, *tail]


def prepare_hook_messages(messages: Optional[List[Dict[str, Any]]],
                          *,
                          cap: bool = True) -> List[Dict[str, Any]]:
    """构造可直接作为 reflect base_messages 的快照：冻结 → 字符护栏 → 发送规整。

    cap=False 用于已自行控制规模的场景；默认开启护栏防巨型上下文。
    返回的消息不含 _layer/_source，且经配对修复/角色归一/prefill 修复，
    与主对话发送口径一致。
    """
    frozen = freeze_messages(messages)
    if cap:
        frozen = cap_snapshot_chars(frozen)
    return normalize_for_send(frozen)
