"""证据数学（纯函数，零 I/O 零依赖）——记忆/反思的"可信度"维度。

证据驱动的可信度数学，适配本项目的 importance
量纲（0.0-1.0）与 metadata 携带方式。职责切分（铁律纪律，不并存两套衰减）：
- consolidator 的 importance × 时间衰减 × 访问强化管「记不记得住」；
- 本模块的 evidence_score 管「可不可信」——反思晋升、注入排序、抑制判定的依据。

模型：确认/反驳双通道，各有独立时钟与半衰期（负面证据记得比正面久），
衰减在读时计算不改存储；protected（PERMANENT / 宪法级）= +inf 不参与衰减。
"""

from __future__ import annotations

import math
import time
from typing import Any, Dict, Optional

from core.config import get_config_float, register_configs_safe

_EVIDENCE_CONFIGS = {
    "memory/evidence": {
        "memory_evidence_rein_half_life_days": {
            "description": "正向证据（确认/复述）衰减半衰期",
            "default": 30.0, "unit": "天", "min": 1.0, "max": 365.0, "advanced": True,
        },
        "memory_evidence_disp_half_life_days": {
            "description": "负向证据（反驳/纠错）衰减半衰期（刻意长于正向：负面记得更久）",
            "default": 180.0, "unit": "天", "min": 7.0, "max": 730.0, "advanced": True,
        },
        "memory_evidence_confirmed_threshold": {
            "description": "反思 pending→confirmed 的证据分阈值",
            "default": 1.0, "advanced": True,
        },
        "memory_evidence_promoted_threshold": {
            "description": "反思 confirmed→晋升人格/图谱的证据分阈值",
            "default": 2.0, "advanced": True,
        },
        "memory_evidence_archive_threshold": {
            "description": "证据分低于该值进入归档倒计时",
            "default": -2.0, "advanced": True,
        },
        "memory_evidence_archive_days": {
            "description": "证据分持续低于阈值多少天后归档",
            "default": 14, "unit": "天", "min": 1, "max": 90,
        },
    },
}

register_configs_safe(_EVIDENCE_CONFIGS)

# metadata 内 evidence 子字典的键（单点定义）
EVIDENCE_KEY = "evidence"
_F_REIN = "reinforcement"
_F_DISP = "disputation"
_F_REIN_AT = "rein_last_signal_at"
_F_DISP_AT = "disp_last_signal_at"
_F_USER_REINFORCE = "user_reinforce_count"
_F_SUB_ZERO_DAYS = "sub_zero_days"
_F_SUB_ZERO_LAST = "sub_zero_last_date"

# 用户确认/复述的信号增量（金标准）
USER_CONFIRM_DELTA = 1.0
# 用户反驳/纠错的信号增量
USER_DISPUTE_DELTA = 1.0
# 同一来源被反复确认的连击加成（计数终生不清零）
_USER_COMBO_THRESHOLD = 2
_USER_COMBO_BONUS = 0.5


def is_protected(entry: Any) -> bool:
    """宪法级条目：PERMANENT 类型或 metadata 显式 protected——豁免衰减/归档/抑制。

    evidence_score 对 protected 返回 +inf（永不因预算/衰减被挤掉）。
    """
    memory_type = getattr(entry, "memory_type", None)
    type_value = getattr(memory_type, "value", memory_type)
    if type_value == "permanent":
        return True
    metadata = getattr(entry, "metadata", None) or {}
    return bool(metadata.get("protected"))


def initial_reinforcement(importance: float) -> float:
    """importance（0-1）→ 初始正向证据种子（写入时的先验可信度）。

    阶梯刻意陡峭：「请记住 X」级（≥0.9）直通快车道，弱线索从零起步——
    让一条强信号能穿越 pending→confirmed→promoted 漏斗，弱信号需要积累。
    """
    if importance >= 0.9:
        return 0.8
    if importance >= 0.8:
        return 0.6
    if importance >= 0.7:
        return 0.4
    if importance >= 0.6:
        return 0.2
    return 0.0


def get_evidence(metadata: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """读取 metadata 中的证据组（缺省补零；返回副本，改动需经 put_evidence 写回）。"""
    raw = (metadata or {}).get(EVIDENCE_KEY) or {}
    return {
        _F_REIN: float(raw.get(_F_REIN, 0.0)),
        _F_DISP: float(raw.get(_F_DISP, 0.0)),
        _F_REIN_AT: float(raw.get(_F_REIN_AT, 0.0)),
        _F_DISP_AT: float(raw.get(_F_DISP_AT, 0.0)),
        _F_USER_REINFORCE: int(raw.get(_F_USER_REINFORCE, 0)),
        _F_SUB_ZERO_DAYS: int(raw.get(_F_SUB_ZERO_DAYS, 0)),
        _F_SUB_ZERO_LAST: str(raw.get(_F_SUB_ZERO_LAST, "")),
    }


def put_evidence(metadata: Dict[str, Any], evidence: Dict[str, Any]) -> Dict[str, Any]:
    """把证据组写回 metadata（返回同一 metadata 对象，链式友好）。"""
    metadata[EVIDENCE_KEY] = evidence
    return metadata


def _decayed(value: float, last_signal_at: float, half_life_days: float, now: float) -> float:
    """按各自时钟做指数衰减（读时计算，不改存储）。"""
    if value <= 0.0:
        return 0.0
    if last_signal_at <= 0.0:
        return value
    elapsed_days = max(0.0, (now - last_signal_at) / 86400.0)
    return value * math.pow(0.5, elapsed_days / half_life_days)


def evidence_score(entry: Any, *, now: Optional[float] = None) -> float:
    """当前证据分 = 有效正向 − 有效负向；protected 恒 +inf。"""
    if is_protected(entry):
        return math.inf
    now = now if now is not None else time.time()
    ev = get_evidence(getattr(entry, "metadata", None))
    eff_rein = _decayed(
        ev[_F_REIN], ev[_F_REIN_AT],
        get_config_float("memory_evidence_rein_half_life_days", 30.0), now,
    )
    eff_disp = _decayed(
        ev[_F_DISP], ev[_F_DISP_AT],
        get_config_float("memory_evidence_disp_half_life_days", 180.0), now,
    )
    return eff_rein - eff_disp


def apply_reinforcement(
    metadata: Dict[str, Any],
    delta: float = USER_CONFIRM_DELTA,
    *,
    user_originated: bool = True,
    now: Optional[float] = None,
) -> Dict[str, Any]:
    """施加正向信号（确认/复述/任务成功佐证）。

    用户来源信号在既有正向上做连击加成（同一事实被反复确认越来越稳）；
    增量先按距上次信号的衰减折算合并（数学上与读时衰减自洽：存储值
    始终换算到"当下等效"再加 delta，避免双时钟漂移）。
    """
    now = now if now is not None else time.time()
    ev = get_evidence(metadata)
    current = _decayed(
        ev[_F_REIN], ev[_F_REIN_AT],
        get_config_float("memory_evidence_rein_half_life_days", 30.0), now,
    )
    bonus = 0.0
    if user_originated:
        ev[_F_USER_REINFORCE] += 1
        if ev[_F_USER_REINFORCE] > _USER_COMBO_THRESHOLD:
            bonus = _USER_COMBO_BONUS
    ev[_F_REIN] = current + delta + bonus
    ev[_F_REIN_AT] = now
    return put_evidence(metadata, ev)


def apply_disputation(
    metadata: Dict[str, Any],
    delta: float = USER_DISPUTE_DELTA,
    *,
    now: Optional[float] = None,
) -> Dict[str, Any]:
    """施加负向信号（反驳/纠错/任务失败归因）。"""
    now = now if now is not None else time.time()
    ev = get_evidence(metadata)
    current = _decayed(
        ev[_F_DISP], ev[_F_DISP_AT],
        get_config_float("memory_evidence_disp_half_life_days", 180.0), now,
    )
    ev[_F_DISP] = current + delta
    ev[_F_DISP_AT] = now
    return put_evidence(metadata, ev)


def tick_sub_zero(metadata: Dict[str, Any], *, today: str) -> Dict[str, Any]:
    """归档倒计时：证据分为负时每日至多 +1（由整理 sweep 调用）。"""
    ev = get_evidence(metadata)
    if ev[_F_SUB_ZERO_LAST] != today:
        ev[_F_SUB_ZERO_DAYS] += 1
        ev[_F_SUB_ZERO_LAST] = today
    return put_evidence(metadata, ev)


def clear_sub_zero(metadata: Dict[str, Any]) -> Dict[str, Any]:
    """证据分回正后清零归档倒计时。"""
    ev = get_evidence(metadata)
    if ev[_F_SUB_ZERO_DAYS]:
        ev[_F_SUB_ZERO_DAYS] = 0
        ev[_F_SUB_ZERO_LAST] = ""
    return put_evidence(metadata, ev)
