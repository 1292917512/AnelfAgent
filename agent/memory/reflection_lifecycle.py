"""反思生命周期 —— 让 REFLECTION 从"AI 随手写"进阶为证据驱动的认知晋升。

机制（证据驱动的反思状态机，长在现有心跳/画像/图谱面上）：
- 对象：带 ``type:reflection`` 标签的活跃记忆（self_reflection 空闲任务的产出）；
- 状态机（metadata["lifecycle"]["status"]）：pending → confirmed → promoted /
  denied；推进由心跳整理周期（consolidator 同拍）驱动，不在对话路径上；
- 证据分：evidence.py 的 rein/disp 双通道读时衰减；初始种子由 importance
  阶梯派生（initial_reinforcement）；protected（PERMANENT/宪法级）不参与；
- 晋升：confirmed 且 score 达标 → LLM 合并决策（promote/merge/reject，
  复用 dedup.light_llm 通道）→ 写入目标画像（自画像 agent:self 或用户画像）
  或图谱关系——晋升是人格生长的唯一通道，AI 不能随手改自己；
- 负向信号：score 低于阈值进入 sub_zero 每日倒计时，到期归档（走既有
  归档/墓碑通道，可恢复）。

幂等纪律：晋升决策按 reflection id 幂等（promoted/denied 为终态，重入安全）；
LLM 失败不降级为直接晋升（防断电静默重复），退避后重试。
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List

from core.config import get_config_float, get_config_int, register_configs_safe
from core.log import log

from . import evidence
from .memory_store import MemoryStore
from .memory_types import MemoryEntry

_LOG_TAG = "记忆"

REFLECTION_TAG = "type:reflection"
"""反思身份的权威判据（标签；self_reflection 任务与 memorize 均经此标记）。"""

_LIFECYCLE_KEY = "lifecycle"
_F_STATUS = "status"
_F_NEXT_ELIGIBLE = "next_eligible_at"
_F_PROMOTE_ATTEMPTS = "promote_attempts"
_F_ABSORBED_INTO = "absorbed_into"

_STATUS_PENDING = "pending"
_STATUS_CONFIRMED = "confirmed"
_STATUS_PROMOTED = "promoted"
_STATUS_DENIED = "denied"
_TERMINAL = {_STATUS_PROMOTED, _STATUS_DENIED}

# 晋升失败退避（分钟）与最大尝试次数（防断电静默重复 + 死信）
_PROMOTE_RETRY_BACKOFF_MINUTES = 30
_PROMOTE_MAX_RETRIES = 5

_LIFECYCLE_CONFIGS = {
    "memory/reflection": {
        "memory_reflection_cooldown_minutes": {
            "description": "反思状态推进的最小间隔（同一反思两次评估之间）",
            "default": 30, "unit": "分钟", "min": 5, "max": 1440, "advanced": True,
        },
        "memory_reflection_promotion_enabled": {
            "description": "反思达标后是否晋升写入画像（晋升是人格生长的唯一通道）",
            "default": True,
        },
    },
}

register_configs_safe(_LIFECYCLE_CONFIGS)


@dataclass
class LifecycleReport:
    """一轮生命周期推进的执行报告。"""

    evaluated: int = 0
    confirmed: int = 0
    promoted: int = 0
    denied: int = 0
    archived: int = 0
    seeded: int = 0
    errors: List[str] = field(default_factory=list)

    def to_log_lines(self) -> List[str]:
        lines: List[str] = []
        if self.seeded:
            lines.append(f"反思种子初始化 {self.seeded} 条")
        if self.confirmed:
            lines.append(f"反思确认 {self.confirmed} 条")
        if self.promoted:
            lines.append(f"反思晋升 {self.promoted} 条")
        if self.denied:
            lines.append(f"反思否决 {self.denied} 条")
        if self.archived:
            lines.append(f"反思归档 {self.archived} 条")
        if self.errors:
            lines.append(f"反思生命周期异常 {len(self.errors)} 项: {'; '.join(self.errors[:3])}")
        return lines


# ----------------------------------------------------------------------
# 生命周期字段存取
# ----------------------------------------------------------------------

def get_lifecycle(entry: MemoryEntry) -> Dict[str, Any]:
    """读取生命周期组（缺省 pending + 立即可评估）。"""
    raw = (entry.metadata or {}).get(_LIFECYCLE_KEY) or {}
    return {
        _F_STATUS: str(raw.get(_F_STATUS, _STATUS_PENDING)),
        _F_NEXT_ELIGIBLE: float(raw.get(_F_NEXT_ELIGIBLE, 0.0)),
        _F_PROMOTE_ATTEMPTS: int(raw.get(_F_PROMOTE_ATTEMPTS, 0)),
        _F_ABSORBED_INTO: str(raw.get(_F_ABSORBED_INTO, "")),
    }


def _put_lifecycle(entry: MemoryEntry, lc: Dict[str, Any]) -> None:
    entry.metadata[_LIFECYCLE_KEY] = lc


def is_reflection(entry: MemoryEntry) -> bool:
    return REFLECTION_TAG in (entry.tags or [])


def reflection_status(entry: MemoryEntry) -> str:
    return get_lifecycle(entry)[_F_STATUS]


def seed_reflection(entry: MemoryEntry) -> bool:
    """新反思写入时初始化证据种子与生命周期（memorize/auto_capture 调用）。

    已初始化（有 lifecycle 组或已有证据）时不重复播种——返回是否发生播种。
    """
    if not is_reflection(entry):
        return False
    if _LIFECYCLE_KEY in (entry.metadata or {}) or evidence.EVIDENCE_KEY in (entry.metadata or {}):
        return False
    evidence.apply_reinforcement(
        entry.metadata,
        evidence.initial_reinforcement(entry.importance),
        user_originated=False,
    )
    _put_lifecycle(entry, get_lifecycle(entry))
    return True


# ----------------------------------------------------------------------
# 心跳推进（consolidator 同拍调用）
# ----------------------------------------------------------------------

_PROMOTE_PROMPT = """\
你是人格晋升裁决器。一条反思已达证据标准，拟晋升为目标画像的正式条目。
请判定如何处理（只输出 JSON）：

- promote：作为新条目追加进画像（新认知，画像未覆盖）
- merge：画像已有相近条目，应合并改写（给出合并后的完整画像文本）
- reject：与画像冲突或不值得固化（给出理由）

【目标画像（{target}）当前内容】
{profile}

【待晋升反思】
{reflection}

输出：{{"action": "promote"/"merge"/"reject", "content": "merge 时的完整画像文本（promote 时给条目文本）", "reason": "一句话理由"}}
"""

_JSON_OBJ_RE = re.compile(r"\{.*\}", re.DOTALL)


async def advance_reflections(store: MemoryStore) -> LifecycleReport:
    """推进一轮全部活跃反思的生命周期（幂等，由 consolidator 调用）。"""
    report = LifecycleReport()
    now = time.time()
    cooldown_s = get_config_int("memory_reflection_cooldown_minutes", 30) * 60

    try:
        entries = await store.search_by_tags([REFLECTION_TAG], limit=200)
    except Exception as exc:
        report.errors.append(f"反思读取失败: {exc}")
        return report

    confirmed_threshold = get_config_float("memory_evidence_confirmed_threshold", 1.0)
    promoted_threshold = get_config_float("memory_evidence_promoted_threshold", 2.0)
    archive_threshold = get_config_float("memory_evidence_archive_threshold", -2.0)
    archive_days = get_config_int("memory_evidence_archive_days", 14)
    today = time.strftime("%Y-%m-%d", time.localtime(now))

    for entry in entries:
        if entry.id is None or not is_reflection(entry) or evidence.is_protected(entry):
            continue
        lc = get_lifecycle(entry)
        if lc[_F_STATUS] in _TERMINAL:
            continue

        # 历史反思补播种（老数据无证据组 → 以 importance 先验起步）
        if evidence.EVIDENCE_KEY not in (entry.metadata or {}):
            seed_reflection(entry)
            report.seeded += 1

        score = evidence.evidence_score(entry, now=now)
        status = lc[_F_STATUS]
        try:
            # 负分归档倒计时（每日至多 +1）
            if score <= archive_threshold:
                evidence.tick_sub_zero(entry.metadata, today=today)
                if evidence.get_evidence(entry.metadata)["sub_zero_days"] >= archive_days:
                    await store.archive_memory(entry.id, "反思证据分长期为负")
                    report.archived += 1
                    continue
            else:
                evidence.clear_sub_zero(entry.metadata)

            # 状态推进（冷却闸门内只落证据不动状态）
            if now < lc[_F_NEXT_ELIGIBLE]:
                await store.update(entry)
                continue
            report.evaluated += 1

            if status == _STATUS_PENDING and score >= confirmed_threshold:
                lc[_F_STATUS] = _STATUS_CONFIRMED
                lc[_F_NEXT_ELIGIBLE] = now + cooldown_s
                report.confirmed += 1
            elif status == _STATUS_CONFIRMED and score >= promoted_threshold:
                outcome = await _try_promote(store, entry, lc, now)
                if outcome == "promoted":
                    report.promoted += 1
                elif outcome == "denied":
                    report.denied += 1
                # retry/blocked：lc 已由 _try_promote 推进
            _put_lifecycle(entry, lc)
            await store.update(entry)
        except Exception as exc:
            report.errors.append(f"#{entry.id} 推进失败: {exc}")
            log(f"反思 #{entry.id} 生命周期推进失败: {exc}", "WARNING", tag=_LOG_TAG)

    return report


async def _try_promote(
    store: MemoryStore,
    entry: MemoryEntry,
    lc: Dict[str, Any],
    now: float,
) -> str:
    """晋升决策：LLM 合并裁决 → 写目标画像；失败退避，不静默晋升。

    Returns: promoted / denied / retry / blocked
    """
    from core.config import get_config_bool
    if not get_config_bool("memory_reflection_promotion_enabled", True):
        return "retry"

    lc[_F_NEXT_ELIGIBLE] = now + _PROMOTE_RETRY_BACKOFF_MINUTES * 60

    from .self_profile import resolve_promotion_target, update_profile_content

    target_scope = resolve_promotion_target(entry)
    current_profile = await _load_profile_text(store, target_scope)

    from .dedup import light_llm
    prompt = (
        _PROMOTE_PROMPT
        .replace("{target}", target_scope)
        .replace("{profile}", current_profile or "（空）")
        .replace("{reflection}", entry.content[:800])
    )
    try:
        raw = await light_llm(prompt)
    except Exception as exc:
        lc[_F_PROMOTE_ATTEMPTS] += 1
        if lc[_F_PROMOTE_ATTEMPTS] >= _PROMOTE_MAX_RETRIES:
            lc[_F_STATUS] = _STATUS_DENIED  # 死信：晋升通道关闭，反思本体保留
            log(f"反思 #{entry.id} 晋升死信: {exc}", "WARNING", tag=_LOG_TAG)
            return "denied"
        log(f"反思 #{entry.id} 晋升决策失败（退避重试）: {exc}", "DEBUG", tag=_LOG_TAG)
        return "retry"

    m = _JSON_OBJ_RE.search(raw or "")
    data: Dict[str, Any] = {}
    if m:
        try:
            data = json.loads(m.group(0))
        except json.JSONDecodeError:
            data = {}
    action = str(data.get("action", "")).strip().lower()
    content = str(data.get("content", "")).strip()

    if action == "reject":
        lc[_F_STATUS] = _STATUS_DENIED
        log(f"反思 #{entry.id} 晋升被拒: {data.get('reason', '')}", "DEBUG", tag=_LOG_TAG)
        return "denied"
    if action in ("promote", "merge") and content:
        if action == "promote":
            new_text = (current_profile.rstrip() + "\n- " + content).strip() if current_profile else content
        else:
            new_text = content
        ok = await update_profile_content(store, target_scope, new_text)
        if ok:
            lc[_F_STATUS] = _STATUS_PROMOTED
            lc[_F_ABSORBED_INTO] = f"profile:{target_scope}"
            log(f"反思 #{entry.id} 已晋升到画像 {target_scope}", "INFO", tag=_LOG_TAG)
            return "promoted"
    # 解析失败/写画像失败：退避重试
    lc[_F_PROMOTE_ATTEMPTS] += 1
    if lc[_F_PROMOTE_ATTEMPTS] >= _PROMOTE_MAX_RETRIES:
        lc[_F_STATUS] = _STATUS_DENIED
        return "denied"
    return "retry"


async def _load_profile_text(store: MemoryStore, target_scope: str) -> str:
    """读取目标画像文本（agent:self 或 user/group 实体，取最新一条画像记忆）。"""
    from .self_profile import SELF_PROFILE_SOURCE, SELF_SCOPE
    if target_scope == SELF_SCOPE:
        source = SELF_PROFILE_SOURCE
    else:
        # 画像镜像的 source 约定为 entity_{频道:ID}（去掉 user:/group: 前缀）
        entity_id = target_scope.split(":", 1)[1] if ":" in target_scope else target_scope
        source = f"entity_{entity_id}"
    entries = await store.list_recent(limit=1, memory_type=None, source=source)
    return entries[0].content if entries else ""

async def load_confirmed_block(store: MemoryStore, *, max_entries: int = 8) -> str:
    """渲染已确认反思的注入块（证据分 >0）。

    已确认认知随画像区主动呈现（人格的"已验证认知"分区）；
    pending/否决/归档不呈现（未确认内容不污染对话）。fail-open 返回空串。
    """
    try:
        entries = await store.search_by_tags([REFLECTION_TAG], limit=50)
    except Exception as exc:
        log(f"已确认反思读取失败: {exc}", "DEBUG", tag=_LOG_TAG)
        return ""
    now = time.time()
    confirmed = [
        e for e in entries
        if reflection_status(e) == _STATUS_CONFIRMED
        and evidence.evidence_score(e, now=now) > 0
    ]
    if not confirmed:
        return ""
    confirmed.sort(key=lambda e: evidence.evidence_score(e, now=now), reverse=True)

    lines = [f"- {e.content[:200]}" for e in confirmed[:max_entries]]
    if not lines:
        return ""
    return "[系统注入·已确认认知]（经证据确认的反思，可自然引用）：\n" + "\n".join(lines)
