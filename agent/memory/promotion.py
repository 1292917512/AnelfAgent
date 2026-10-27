"""证据达标后的画像晋升决策与恢复，不参与证据评分。

模型看到：完整反思、涉及对象和目标当前画像；明确区分认知主体与对话参与者。
Token：每次新晋升一次主体选择、一次画像合成；已提交操作重试不调用模型。
缓存：内部轻量调用，不改变主对话稳定前缀。
"""

from __future__ import annotations

import hashlib
import json
import time
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict

from agent.storage.profile_store import ProfileConflict

from .memory_store import MemoryStore
from .memory_types import MemoryEntry
from .profiles import ProfileWriter
from .self_profile import SELF_SCOPE


class _Subject(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    target: str
    reason: str


class _Decision(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    action: Literal["promote", "merge", "reject"]
    content: str
    reason: str


async def promote_reflection(
    store: MemoryStore, profiles: ProfileWriter, entry: MemoryEntry, lifecycle: dict[str, Any],
) -> str:
    """先持久化决策，再提交画像，最后推进生命周期；任何一步都可从回执续跑。"""
    from .dedup import light_llm

    source_hash = hashlib.sha256(json.dumps([entry.content, sorted(entry.tags)], ensure_ascii=False).encode()).hexdigest()
    plan = lifecycle.get("promotion")
    if isinstance(plan, dict):
        result = await profiles.resume(str(plan["operation_id"]))
        if result is not None:
            if not result.synced:
                return "retry"
            if plan.get("source_hash") != source_hash:
                lifecycle.pop("promotion", None)
                lifecycle["error"] = "旧认知的画像操作已完成；当前反思正文已修订，需重新评估"
                return "retry"
            lifecycle.update(status="promoted", absorbed_into=f"profile:{plan['target']}",
                             reason=plan["reason"], operation_id=result.operation_id)
            lifecycle.pop("promotion", None)
            return "promoted"
        if plan.get("source_version") != entry.version:
            lifecycle.pop("promotion", None)
            lifecycle["error"] = "晋升决策后反思已变化，等待重新评估"
            return "retry"
    else:
        targets = sorted({SELF_SCOPE, *(t for t in entry.tags if t.startswith(("user:", "group:")))})
        subject = _Subject.model_validate_json(await light_llm(
            "选择这条反思的认知主体，只输出 JSON {\"target\":\"候选之一或 none\",\"reason\":\"理由\"}。"
            "标签是参与者，不等于主体；关于 AI 的稳定行为与认知才归 agent:self；"
            "关于用户/群组的长期事实归相应对象；运行日志、巡检完成、临时任务状态、"
            "缺少对象标识或无法明确归属的认知选择 none，不得猜测身份。\n"
            + json.dumps({"candidates": targets, "reflection": entry.content,
                          "evidence": entry.metadata.get("evidence", {})}, ensure_ascii=False)
        ))
        if subject.target == "none":
            lifecycle.update(status="denied", reason=subject.reason)
            return "denied"
        if subject.target not in targets:
            raise ValueError("晋升目标不在反思涉及的身份范围内")
        scope_type, scope_id = subject.target.split(":", 1)
        current = await profiles.read(scope_type, scope_id)
        decision = _Decision.model_validate_json(await light_llm(
            "判断如何将已达证据标准的反思纳入目标画像。只输出 JSON："
            '{"action":"promote/merge/reject","content":"文本","reason":"具体依据"}。'
            "promote 给出值得新增的认知条目；merge 给出合并后的完整画像，保留未被更正的事实与条件；"
            "新证据确实纠正旧认识时允许修订，不能仅因与旧画像不同就否决。"
            "reject 表示不值得固化或证据不足；运行汇报不能成为人格。\n"
            + json.dumps({"target": f"{current['scope_type']}:{current['scope_id']}",
                          "profile": current["personality"], "reflection": entry.content,
                          "evidence": entry.metadata.get("evidence", {}),
                          "subject_reason": subject.reason}, ensure_ascii=False)
        ))
        if decision.action == "reject":
            lifecycle.update(status="denied", reason=decision.reason)
            return "denied"
        content = decision.content.strip()
        if not content or not decision.reason.strip():
            raise ValueError("晋升决策缺少正文或依据")
        if decision.action == "promote" and current["personality"]:
            content = current["personality"].rstrip() + "\n- " + content
        plan = {
            "operation_id": uuid4().hex, "target": f"{current['scope_type']}:{current['scope_id']}",
            "scope_type": current["scope_type"], "scope_id": current["scope_id"],
            "revision": current["revision"], "content": content, "reason": decision.reason,
            "action": decision.action, "source_version": entry.version + 1,
            "source_hash": source_hash,
        }
        lifecycle["promotion"] = plan
        lifecycle["decision_at"] = time.time()
        entry.metadata["lifecycle"] = lifecycle
        if not await store.update(entry, expected_version=entry.version, actor="reflection_decision"):
            raise ValueError("晋升决策期间反思已变化")
    try:
        result = await profiles.write(
            plan["scope_type"], plan["scope_id"], plan["content"], expected_revision=plan["revision"],
            actor="reflection", reason=plan["reason"], operation_id=plan["operation_id"],
            source_memory_id=entry.id, source_version=entry.version,
        )
    except ProfileConflict:
        lifecycle.pop("promotion", None)
        raise
    if not result.synced:
        return "retry"
    lifecycle.update(status="promoted", absorbed_into=f"profile:{plan['target']}",
                     reason=plan["reason"], operation_id=result.operation_id)
    lifecycle.pop("promotion", None)
    lifecycle.pop("error", None)
    return "promoted"
