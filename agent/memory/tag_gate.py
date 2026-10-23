"""标签写入闸门：写入路径的确定性防线（只治理增量，永不改写既有形态）。

分层职责：
- 前缀 lane：type/goal/date 结构校验、user/group 实体标签形态校验、
  未知前缀拒收——维护前缀语义体系的完整性（多用户归属标签只验形态，
  归属对错由每日治理任务按「张冠李戴」议程裁决）；
- topic：归一精确匹配改写 → 完整泛义词拒收 → 包含关系提示。
  不剥除中文词缀，不把词面包含关系当作语义等价。

核心不变量：自动改写与拒收只作用于 df==0 的新标签，改写目标必须是
df>=2 的既有形态；双侧都已立住的分裂对留给归并议程（事实归系统、
决策归 AI）。全程 fail-open：任何异常原样放行，永不阻塞写入。

Model Experience：写入工具返回标签处理摘要，治理工具仅返回建议；实体标签无效时不猜测归属。
Token：不新增模型调用，摘要最多五项改写/拒收和三条建议；提取词表默认最多 80 词。
缓存：摘要只进入工具结果，词表只进入内部提取调用，不改变主对话稳定前缀。
观测计数进入 memory metrics；咨询不计为实际改写或拒收。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

from agent.messages import build_entity_scope, parse_entity_scope
from core.config import get_config, get_config_bool, get_config_int, register_configs_safe
from core.log import log

from .store.tag_intel import normalize_tag_form

if TYPE_CHECKING:
    from .memory_store import MemoryStore

# 允许的标签前缀（体系外前缀一律拒收）
_ALLOWED_PREFIXES = ("type:", "user:", "group:", "topic:", "goal:", "date:", "main:")

# type 前缀的合法取值（与 memorize 的 _TYPE_MAP 一致）
_TYPE_VALUES = frozenset(("fact", "event", "trait", "entity", "reflection", "permanent"))

# 自动改写目标的最低文档频率；源标签只要 df>0 就受保护。
_ESTABLISHED_DF = 2

# 泛义核心词：整名命中即拒收。记忆的组织维度已由 type:/user:/date: 等
# 结构标签承担，这类词作 topic 没有区分度，只会稀释标签空间
_GENERIC_CORE = frozenset(
    (
        "东西",
        "事情",
        "问题",
        "情况",
        "内容",
        "资料",
        "信息",
        "记录",
        "笔记",
        "操作",
        "方法",
        "方式",
        "步骤",
        "工具",
        "结果",
        "目标",
        "任务",
        "话题",
        "经验",
        "时间",
        "时候",
        "地点",
        "地方",
        "方面",
        "部分",
        "种类",
        "想法",
        "感觉",
        "习惯",
        "日常",
        "生活",
        "工作",
        "学习",
        "知识",
        "技术",
        "细节",
        "变化",
        "状态",
        "清单",
        "总结",
        "技巧",
        "事件",
        "事实",
        "数据",
        "文件",
        "计划",
        "过程",
        "动态",
        "近况",
        "安排",
        "需求",
        "用户",
        "助手",
        "机器人",
        "系统",
        "记忆",
        "消息",
        "会话",
        "对话",
    )
)

# 泛义修饰词仅在整词命中时拒收，绝不从话题名两端剥除。
_GENERIC_MODS = frozenset(
    (
        "新",
        "旧",
        "大",
        "小",
        "多",
        "少",
        "好",
        "坏",
        "重要",
        "主要",
        "次要",
        "关键",
        "基本",
        "简单",
        "特别",
        "一般",
        "常见",
        "唯一",
        "可能",
        "很多",
        "一些",
        "各种",
        "各类",
        "不同",
        "相同",
        "其他",
        "另外",
        "相关",
        "有关",
        "关于",
        "第一次",
        "最后一次",
        "最近",
        "之前",
        "之后",
        "临时",
        "长期",
        "短期",
        "定期",
        "每天",
        "一点",
        "有点",
    )
)

_HINTS_CAP = 3


@dataclass
class TagGateResult:
    """闸门结果：落地形态 + 动作明细（工具返回里摘要给 LLM 自我纠正）。"""

    tags: List[str]
    rewrites: List[Dict[str, str]] = field(default_factory=list)
    hints: List[str] = field(default_factory=list)
    dropped: List[Dict[str, str]] = field(default_factory=list)

    def summary(self) -> Optional[Dict[str, Any]]:
        """工具结果的闸门摘要；无动作返回 None 不占字段。"""
        if not (self.rewrites or self.hints or self.dropped):
            return None
        return {
            "rewrites": self.rewrites[:5],
            "hints": self.hints[:_HINTS_CAP],
            "dropped": self.dropped[:5],
        }


async def enforce_tag_gate(
    tags: List[str],
    *,
    store: MemoryStore,
    enforce: bool = True,
) -> TagGateResult:
    """写入路径闸门入口（memorize / auto_capture 用 enforce 模式）。

    enforce=False 为纯咨询（update_memory 治理工具用）：不改任何标签，
    动作明细转为建议输出。统计或规则处理失败整体降级放行，取消正常向上传播。
    """
    try:
        if not tags or not get_config_bool("memory_tag_gate_enabled", True):
            return TagGateResult(tags=list(tags))
        tag_df = await store.list_tags(fresh=True)
        result = apply_tag_gate(tags, tag_df, enforce=enforce)
        _count_metrics(result)
        return result
    except Exception as exc:
        _incr("write.tag_gate_failopen")
        log(f"标签闸门降级放行: {exc}", "DEBUG", tag="记忆")
        return TagGateResult(tags=list(tags))


def apply_tag_gate(
    tags: List[str],
    tag_df: Dict[str, int],
    *,
    enforce: bool = True,
) -> TagGateResult:
    """按给定文档频率评估标签；已有形态不变，语义关系只给建议。"""
    result = TagGateResult(tags=[])
    generic, whitelist = _active_vocab()
    unique = list(dict.fromkeys(tags))

    norm_index = _established_topic_index(tag_df)
    for tag in unique:
        if tag_df.get(tag, 0) > 0:
            if tag not in result.tags:
                result.tags.append(tag)
            continue
        verdict = _check_lane(tag)
        if verdict is not None:
            result.dropped.append({"tag": tag, "reason": verdict})
            continue
        if tag.startswith("topic:"):
            effective = _gate_topic(tag, tag_df, generic, whitelist, norm_index, result)
            if effective is None:
                continue  # 拒收，明细已记
            if effective not in result.tags:
                result.tags.append(effective)
            continue
        result.tags.append(tag)

    if not enforce:
        result.hints = [
            *(f"{r['tag']}：{r['reason']}，建议核对" for r in result.dropped),
            *(f"{r['from']} 与既有 {r['to']} 为{r['rule']}，建议改用后者" for r in result.rewrites),
            *result.hints,
        ]
        result.rewrites = []
        result.dropped = []
        result.tags = unique
    result.hints = result.hints[:_HINTS_CAP]
    return result


# ------------------------------------------------------------------
# 前缀 lane
# ------------------------------------------------------------------


def _check_lane(tag: str) -> Optional[str]:
    """新标签的结构校验；返回 None 表示放行，否则为拒收理由。"""
    if not tag.startswith(_ALLOWED_PREFIXES):
        return "未知前缀"
    prefix, _, value = tag.partition(":")
    if not value.strip():
        return "空前缀值"
    if prefix == "type" and value not in _TYPE_VALUES:
        return "未知类型"
    if prefix in ("user", "group"):
        adapter, separator, entity_id = value.partition(":")
        if not separator or not adapter or any(char.isspace() for char in value):
            return "实体标签须为 user:频道:ID 或 group:频道:ID"
        if not parse_entity_scope(build_entity_scope(prefix, adapter, entity_id))[2]:
            return "实体 ID 不能为空"
    if prefix == "date":
        try:
            if date.fromisoformat(value).isoformat() != value:
                return "日期格式非法"
        except ValueError:
            return "日期格式非法"
    return None


# ------------------------------------------------------------------
# topic tier
# ------------------------------------------------------------------


def _gate_topic(
    rep: str,
    tag_df: Dict[str, int],
    generic: frozenset[str],
    whitelist: frozenset[str],
    norm_index: Dict[str, List[str]],
    result: TagGateResult,
) -> Optional[str]:
    """只处理未出现的话题；目标歧义或语义关系均保留原标签。"""
    targets = norm_index.get(normalize_tag_form(rep, preserve_word_boundaries=True), [])
    if len(targets) == 1:
        result.rewrites.append({"from": rep, "to": targets[0], "rule": "写法变体"})
        return targets[0]
    if len(targets) > 1:
        result.hints.append(f"新标签 {rep} 与多个既有标签同形（{'/'.join(sorted(targets))}），请确认归并目标")
        return rep

    if rep not in whitelist:
        # 只拒收完整泛义词，不推断复合词内部的语义。
        name = rep[len("topic:") :]
        if name in generic:
            result.dropped.append({"tag": rep, "reason": "泛义词"})
            return None

    # Tier-3：包含关系提示（语义归并归 AI 议程）
    hint = _containment_hint(rep, tag_df)
    if hint and len(result.hints) < _HINTS_CAP:
        result.hints.append(hint)
    return rep


def _established_topic_index(tag_df: Dict[str, int]) -> Dict[str, List[str]]:
    """df>=2 的 topic 标签按归一形态建索引（Tier-1 查表）。"""
    index: Dict[str, List[str]] = {}
    for tag, count in tag_df.items():
        if count >= _ESTABLISHED_DF and tag.startswith("topic:"):
            index.setdefault(normalize_tag_form(tag, preserve_word_boundaries=True), []).append(tag)
    return index


def _containment_hint(rep: str, tag_df: Dict[str, int]) -> str:
    """与既有高频 topic 名互为子串时的归并建议（对齐归并议程包含关系口径）。"""
    name = rep[len("topic:") :]
    if len(name) < 2:
        return ""
    best: Optional[Tuple[str, int]] = None
    for tag, count in tag_df.items():
        if count < _ESTABLISHED_DF or not tag.startswith("topic:"):
            continue
        other = tag[len("topic:") :]
        if other and other != name and (name in other or other in name):
            if best is None or count > best[1]:
                best = (tag, count)
    if best is None:
        return ""
    return f"新标签 {rep} 与既有 {best[0]}（{best[1]} 次）名称有包含关系，含义可能不同，请核对后决定是否归并"


def _active_vocab() -> tuple[frozenset[str], frozenset[str]]:
    """生效词表：内置词表 + 运行时补充；白名单词豁免 Tier-2。"""
    core = _GENERIC_CORE | frozenset(
        tag.removeprefix("topic:") for tag in _split_config("memory_tag_gate_blacklist_extra")
    )
    generic = core | _GENERIC_MODS | _split_config("memory_tag_gate_mods_extra")
    return generic, _split_config("memory_tag_gate_whitelist")


def _split_config(key: str) -> frozenset[str]:
    raw = str(get_config(key, "") or "")
    return frozenset(part.strip() for part in re.split(r"[,，、]", raw) if part.strip())


def _count_metrics(result: TagGateResult) -> None:
    if result.rewrites:
        _incr("write.tag_gate_rewrite", len(result.rewrites))
    if result.dropped:
        _incr("write.tag_gate_drop", len(result.dropped))
    if result.hints:
        _incr("write.tag_gate_hint", len(result.hints))


def _incr(key: str, n: int = 1) -> None:
    from . import metrics

    metrics.incr(key, n)


def topic_vocab_line(tag_df: Dict[str, int], *, limit: Optional[int] = None) -> str:
    """高 df 主题标签词表（顿号连接；空表返回空串）——提取 prompt 的复用词表。"""
    cap = limit if limit is not None else get_config_int("memory_tag_gate_vocab_size", 80)
    ranked = sorted(
        (
            tag
            for tag, count in tag_df.items()
            if count >= _ESTABLISHED_DF and tag.startswith("topic:") and len(tag) > len("topic:")
        ),
        key=lambda tag: (-tag_df.get(tag, 0), tag),
    )
    return "、".join(tag[len("topic:") :] for tag in ranked[: max(0, cap)])


# ------------------------------------------------------------------
# 配置注册
# ------------------------------------------------------------------

_GATE_CONFIGS = {
    "memory/tag-gate": {
        "memory_tag_gate_enabled": {
            "description": "写入侧标签闸门（只治理新标签：写法变体归一/泛义词拒收/前缀校验），异常自动降级放行",
            "default": True,
        },
        "memory_tag_gate_vocab_injection": {
            "description": "自动提取的 topic 词表注入（高 df 既有主题词喂给提取器，生成期抑制近义变体）",
            "default": True,
        },
        "memory_tag_gate_vocab_size": {
            "description": "提取词表注入的最大词数",
            "default": 80,
            "advanced": True,
            "unit": "个",
        },
        "memory_tag_gate_blacklist_extra": {
            "description": "泛义核心词补充（完整标签名，逗号/顿号分隔）",
            "default": "",
            "advanced": True,
        },
        "memory_tag_gate_mods_extra": {
            "description": "泛义修饰词补充（topic 名，逗号/顿号分隔；仅整词拒收，不剥除词缀）",
            "default": "",
            "advanced": True,
        },
        "memory_tag_gate_whitelist": {
            "description": "闸门白名单（完整标签名，跳过泛义词拒收；误杀救援用）",
            "default": "",
            "advanced": True,
        },
    },
}

register_configs_safe(_GATE_CONFIGS)
