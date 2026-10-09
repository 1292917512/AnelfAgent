"""记录未匹配停止表达，供后续与模型工具事实关联复核。"""

from __future__ import annotations

from dataclasses import dataclass
from time import monotonic

from core.log import log


@dataclass(frozen=True)
class StopCandidate:
    """一条疑似停止请求及其入站时间。"""

    message_id: str
    text: str
    classification: str
    created_at: float


class StopCandidateLedger:
    """保存有界、短时的未匹配停止候选。"""

    def __init__(self, *, max_entries: int = 256, ttl_seconds: float = 900.0) -> None:
        self._max_entries = max_entries
        self._ttl_seconds = ttl_seconds
        self._candidates: dict[str, StopCandidate] = {}

    def record(self, message_id: str, text: str, classification: str) -> None:
        """记录一条未匹配候选；没有消息 ID 时不建立无法关联的记录。"""
        if not message_id:
            return
        self._prune()
        candidate = StopCandidate(message_id, text, classification, monotonic())
        self._candidates[message_id] = candidate
        while len(self._candidates) > self._max_entries:
            self._candidates.pop(next(iter(self._candidates)))
        log(
            f"MC_STOP_CANDIDATE message_id={message_id} classification={classification} text={text!r}",
            "DEBUG",
            tag="Minecraft",
        )

    def resolve(self, message_id: str, *, request_id: str, tool: str, stopped: object) -> dict[str, object] | None:
        """关联后续停止事实并返回诊断快照；找不到候选时不产生记录。"""
        self._prune()
        candidate = self._candidates.pop(message_id, None)
        if candidate is None:
            return None
        elapsed_ms = (monotonic() - candidate.created_at) * 1000
        result: dict[str, object] = {
            "message_id": message_id,
            "request_id": request_id,
            "text": candidate.text,
            "classification": candidate.classification,
            "tool": tool,
            "stopped": stopped,
            "elapsed_ms": round(elapsed_ms, 1),
        }
        log(
            "MC_STOP_CANDIDATE_RESOLVED "
            f"message_id={message_id} request_id={request_id} classification={candidate.classification} "
            f"tool={tool} stopped={stopped!r} elapsed_ms={elapsed_ms:.1f} text={candidate.text!r}",
            "INFO",
            tag="Minecraft",
        )
        return result

    def _prune(self) -> None:
        cutoff = monotonic() - self._ttl_seconds
        for message_id, candidate in list(self._candidates.items()):
            if candidate.created_at < cutoff:
                del self._candidates[message_id]


def classify_unmatched_stop(text: str) -> str | None:
    """为未匹配文本提供复核分类，不把它升级成确定性命令。"""
    normalized = text.strip().strip("！!。~～…，,；;：:、").casefold()
    if not normalized or not any(marker in normalized for marker in ("停", "暂停", "停止", "stop", "cancel")):
        return None
    if any(phrase in normalized for phrase in ("别停", "不要停", "不用停", "不必停", "别停止", "不要停止")):
        return "negation"
    if any(marker in normalized for marker in ("然后", "再", "之后", "以后", "并且", "同时", "，", ",", ";", "；")):
        return "composite"
    return "unmatched"


stop_candidates = StopCandidateLedger()
