"""反思生命周期 —— 让 REFLECTION 从"AI 随手写"进阶为证据驱动的认知晋升。

机制（证据驱动的反思状态机，长在现有心跳/画像/图谱面上）：
- 对象：带 ``type:reflection`` 标签的活跃记忆（self_reflection 空闲任务的产出）；
- 状态机（metadata["lifecycle"]["status"]）：pending → confirmed → promoted /
  denied；推进由心跳整理周期（consolidator 同拍）驱动，不在对话路径上；
- 证据分：evidence.py 的 rein/disp 双通道读时衰减；初始种子由 importance
  阶梯派生（initial_reinforcement）；protected（PERMANENT/宪法级）不参与；
- 反馈回路：pending/confirmed 反思经 load_verification_block 呈现进对话
  （AI 自然求证），auto_capture 周期用 classify_reflection_feedback 把用户
  回应分类为 confirmed/denied/ignored 并回流证据——负向信号由此进系统，
  sub_zero 归档倒计时随之通电；
- 晋升：confirmed 且 score 达标 → LLM 合并决策（promote/merge/reject，
  复用 dedup.light_llm 通道）→ 写入目标画像（自画像 agent:self 或用户画像）
  或图谱关系——晋升是人格生长的唯一通道，AI 不能随手改自己；
- 负向信号：score 低于阈值进入 sub_zero 每日倒计时，到期归档（走既有
  归档/墓碑通道，可恢复）。

可见性规则：带实体标签（user:*/group:*）的反思只在对应 scope 的会话
可见（含群聊中提到的参与者）；无实体标签的自认知全局可见——A 的
反思绝不注入 B 的对话。

幂等纪律：晋升决策按 reflection id 幂等（promoted/denied 为终态，重入安全）；
LLM 失败不降级为直接晋升（防断电静默重复），退避后重试。
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Set

from core.config import get_config_bool, get_config_float, get_config_int, register_configs_safe
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
_F_SURFACED_AT = "surfaced_at"
_F_FEEDBACK = "feedback"

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
        "memory_reflection_surface_enabled": {
            "description": "是否在对话中呈现待验证认知（AI 自然求证→用户回应回流证据）",
            "default": True,
        },
        "memory_reflection_surface_top_k": {
            "description": "每轮最多呈现的待验证认知条数",
            "default": 3, "min": 1, "max": 8, "unit": "条", "advanced": True,
        },
        "memory_reflection_surface_cooldown_hours": {
            "description": "同一条认知两次呈现的最小间隔（防追问骚扰）",
            "default": 24, "min": 1, "max": 168, "unit": "小时", "advanced": True,
        },
        "memory_reflection_feedback_enabled": {
            "description": "是否对已呈现认知跑用户回应分类（auto_capture 周期）",
            "default": True,
        },
    },
}

register_configs_safe(_LIFECYCLE_CONFIGS)


def scope_tags_from_entity_scopes(entity_scopes: List[str]) -> Set[str]:
    """会话 entity scope（user_qq:123）→ 实体标签集合（user:qq:123）。

    lifecycle 可见性判定的统一口径：反思带的实体标签 ∈ 可见标签集才注入。
    会话后缀（#session）剥除——同一用户的多会话可见性一致。
    """
    tags: Set[str] = set()
    for scope in entity_scopes:
        base = scope.split("#", 1)[0]
        if base.startswith("user_"):
            tags.add(f"user:{base[5:]}")
        elif base.startswith("group_"):
            tags.add(f"group:{base[6:]}")
    return tags


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
    """读取生命周期组（缺省 pending + 立即可评估 + 未呈现）。"""
    raw = (entry.metadata or {}).get(_LIFECYCLE_KEY) or {}
    return {
        _F_STATUS: str(raw.get(_F_STATUS, _STATUS_PENDING)),
        _F_NEXT_ELIGIBLE: float(raw.get(_F_NEXT_ELIGIBLE, 0.0)),
        _F_PROMOTE_ATTEMPTS: int(raw.get(_F_PROMOTE_ATTEMPTS, 0)),
        _F_ABSORBED_INTO: str(raw.get(_F_ABSORBED_INTO, "")),
        _F_SURFACED_AT: float(raw.get(_F_SURFACED_AT, 0.0)),
        _F_FEEDBACK: str(raw.get(_F_FEEDBACK, "")),
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
                    await store.archive_memory(entry.id, "反思证据分长期为负", actor="reflection_lifecycle")
                    report.archived += 1
                    continue
            else:
                evidence.clear_sub_zero(entry.metadata)

            # 状态推进（冷却闸门内只落证据不动状态）
            if now < lc[_F_NEXT_ELIGIBLE]:
                await store.update(entry, actor="reflection_lifecycle")
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
            await store.update(entry, actor="reflection_lifecycle")
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


def _entity_tags(entry: MemoryEntry) -> List[str]:
    """反思携带的实体归属标签（user:*/group:*）。"""
    from .store.tag_intel import ENTITY_PREFIXES
    return [t for t in (entry.tags or []) if t.startswith(ENTITY_PREFIXES)]


def _visible_in(entry: MemoryEntry, visible_tags: Set[str]) -> bool:
    """可见性：带实体标签的反思须命中可见集；无实体标签（自认知）全局可见。"""
    tags = _entity_tags(entry)
    return not tags or any(t in visible_tags for t in tags)


async def _load_reflection_pool(store: MemoryStore, *, limit: int = 50) -> List[MemoryEntry]:
    try:
        return await store.search_by_tags([REFLECTION_TAG], limit=limit)
    except Exception as exc:
        log(f"反思读取失败: {exc}", "DEBUG", tag=_LOG_TAG)
        return []


async def load_confirmed_block(
    store: MemoryStore, *, visible_tags: Set[str], max_entries: int = 8,
) -> str:
    """渲染已确认反思的注入块（证据分 >0 且当前会话可见）。

    已确认认知随画像区主动呈现（人格的"已验证认知"分区）；pending/
    否决/归档不呈现（未确认内容不污染对话）。fail-open 返回空串。
    """
    entries = await _load_reflection_pool(store)
    now = time.time()
    confirmed = [
        e for e in entries
        if reflection_status(e) == _STATUS_CONFIRMED
        and evidence.evidence_score(e, now=now) > 0
        and _visible_in(e, visible_tags)
    ]
    if not confirmed:
        return ""
    confirmed.sort(key=lambda e: evidence.evidence_score(e, now=now), reverse=True)

    lines = [f"- {e.content[:200]}" for e in confirmed[:max_entries]]
    if not lines:
        return ""
    return "[系统注入·已确认认知]（经证据确认的反思，可自然引用）：\n" + "\n".join(lines)


async def load_verification_block(
    store: MemoryStore, *, visible_tags: Set[str],
) -> str:
    """呈现待验证认知（证据为正、冷却已过的 pending/confirmed 反思）。

    呈现即登记 surfaced_at（重置 feedback）——后续 auto_capture 周期把该
    scope 的用户新消息对着"已呈现未裁决"条目跑 LLM 分类，回流的确认/
    反驳就是证据系统此前缺失的真实用户信号。呈现登记失败则跳过该条
    （未登记不会被分类，下轮重新呈现，不会出现"注入了却无人裁决"）。
    """
    if not get_config_bool("memory_reflection_surface_enabled", True):
        return ""
    entries = await _load_reflection_pool(store)
    if not entries:
        return ""
    now = time.time()
    cooldown_s = get_config_int("memory_reflection_surface_cooldown_hours", 24) * 3600
    top_k = max(1, get_config_int("memory_reflection_surface_top_k", 3))

    candidates: List[Any] = []
    for e in entries:
        if e.id is None or evidence.is_protected(e):
            continue
        lc = get_lifecycle(e)
        if lc[_F_STATUS] not in (_STATUS_PENDING, _STATUS_CONFIRMED):
            continue
        if not _visible_in(e, visible_tags):
            continue
        if lc[_F_SURFACED_AT] and now - lc[_F_SURFACED_AT] < cooldown_s:
            continue
        if evidence.evidence_score(e, now=now) <= 0:
            continue
        candidates.append((e, lc))
    if not candidates:
        return ""
    candidates.sort(key=lambda pair: evidence.evidence_score(pair[0], now=now), reverse=True)

    lines: List[str] = []
    for entry, lc in candidates[:top_k]:
        lc[_F_SURFACED_AT] = now
        lc[_F_FEEDBACK] = ""
        _put_lifecycle(entry, lc)
        try:
            await store.update(entry, actor="reflection_lifecycle")
        except Exception as exc:
            log(f"待验证认知呈现登记失败 #{entry.id}: {exc}", "DEBUG", tag=_LOG_TAG)
            continue
        lines.append(f"- {entry.content[:200]}")
    if not lines:
        return ""
    log(f"待验证认知呈现: {len(lines)} 条", "DEBUG", tag=_LOG_TAG)
    return (
        "[系统注入·待验证认知] 以下认知尚不确定，请在对话自然的时机向对方求证"
        "（像聊天一样顺口带出，不要生硬罗列或一次全问）；对方的回应会被系统记录为证据：\n"
        + "\n".join(lines)
    )


def record_user_feedback(entry: MemoryEntry, verdict: str) -> None:
    """对一条反思直接施加用户反馈信号（只改内存对象，落盘由调用方 update）。

    verdict 取 confirmed / denied / ignored（Web 人审的 confirm/dispute 由
    调用方映射）：证据增量 + lifecycle.feedback 登记（防重复回流）。
    """
    if verdict == "confirmed":
        evidence.apply_reinforcement(entry.metadata, 1.0, user_originated=True)
    elif verdict == "denied":
        evidence.apply_disputation(entry.metadata, 1.0)
    elif verdict == "ignored":
        # 忽略是弱负向：轻微降低先验（不计连击，见 evidence 信号语义）
        evidence.apply_reinforcement(entry.metadata, -0.2, user_originated=False)
    else:
        raise ValueError(f"非法反馈判定: {verdict!r}")
    lc = get_lifecycle(entry)
    lc[_F_FEEDBACK] = verdict
    _put_lifecycle(entry, lc)


_FEEDBACK_PROMPT = """\
你是认知反馈分类器。此前系统向用户呈现过几条"待验证认知"，下面是该会话
此后的用户消息。请逐条判断用户对相应认知的实际态度（只输出 JSON）：

- confirmed：用户确认属实（正面回应/顺着说/补充细节）
- denied：用户否认或纠正（"不是这样的"/"其实…"并给出相反事实）
- ignored：用户没有回应或答非所问（无法判断时一律用它）

【已呈现的认知（id | 内容）】
{items}

【此后的用户消息】
{messages}

输出：[{{"id": 认知id, "verdict": "confirmed"/"denied"/"ignored"}}]
"""

_JSON_ARR_RE = re.compile(r"\[.*\]", re.DOTALL)


def _parse_feedback(raw: str) -> Dict[int, str]:
    """解析分类输出为 {id: verdict}（容错：夹带文本/缺字段/非法 id）。"""
    m = _JSON_ARR_RE.search(raw)
    if not m:
        return {}
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return {}
    result: Dict[int, str] = {}
    if isinstance(data, list):
        for item in data:
            if not isinstance(item, dict):
                continue
            try:
                result[int(str(item.get("id")))] = str(item.get("verdict", "")).strip().lower()
            except (TypeError, ValueError):
                continue
    return result


async def classify_reflection_feedback(
    store: MemoryStore, scope_key: str, messages: List[Dict[str, Any]],
) -> int:
    """对"已呈现未裁决"的反思跑用户回应分类，回流证据分。

    由 auto_capture 周期调用（同批对话材料顺手裁决）；scope_key 为
    auto_capture 的会话键（user_qq:123 / group_qq:456 形式）。
    只裁决"呈现先于本批消息"的条目（同批刚呈现的留给下一批），
    无候选或无用户消息时零成本返回。返回实际回流条数。
    """
    if not get_config_bool("memory_reflection_feedback_enabled", True):
        return 0
    user_texts = [
        str(m.get("content", "")).strip()
        for m in messages if m.get("role") == "user"
    ]
    user_texts = [t for t in user_texts if t]
    if not user_texts:
        return 0
    visible_tags = scope_tags_from_entity_scopes([scope_key.replace(":", "_", 1)])
    batch_since = min(
        (int(m.get("ts_ns", 0)) / 1e9 for m in messages if m.get("ts_ns")),
        default=time.time(),
    )

    entries = await _load_reflection_pool(store)
    pending_review: List[MemoryEntry] = []
    for e in entries:
        if e.id is None or evidence.is_protected(e):
            continue
        lc = get_lifecycle(e)
        if lc[_F_STATUS] in _TERMINAL or not lc[_F_SURFACED_AT]:
            continue
        if lc[_F_FEEDBACK]:
            continue
        if lc[_F_SURFACED_AT] >= batch_since:
            continue
        if not _visible_in(e, visible_tags):
            continue
        pending_review.append(e)
    if not pending_review:
        return 0

    items = "\n".join(f"{e.id} | {e.content[:120]}" for e in pending_review)
    rendered = "\n".join(t[:200] for t in user_texts[-12:])
    prompt = _FEEDBACK_PROMPT.replace("{items}", items).replace("{messages}", rendered)
    from .dedup import light_llm
    try:
        raw = await light_llm(prompt, temperature=0.1, timeout=60.0)
    except Exception as exc:
        log(f"反馈分类调用失败 [{scope_key}]: {exc}", "DEBUG", tag=_LOG_TAG)
        return 0

    verdicts = _parse_feedback(raw or "")
    applied = 0
    from . import metrics
    for entry in pending_review:
        verdict = verdicts.get(int(entry.id or 0))
        if verdict not in ("confirmed", "denied", "ignored"):
            verdict = "ignored"
        record_user_feedback(entry, verdict)
        try:
            await store.update(entry, actor="reflection_lifecycle")
            applied += 1
        except Exception as exc:
            log(f"反馈回流写入失败 #{entry.id}: {exc}", "DEBUG", tag=_LOG_TAG)
        metrics.incr(f"reflection.feedback_{verdict}")
    if applied:
        log(f"反思反馈回流 [{scope_key}]: {applied} 条", "DEBUG", tag=_LOG_TAG)
    return applied
