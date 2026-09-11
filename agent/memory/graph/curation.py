"""图谱治理议程：治理事实的语义组装，供 AI 策展决策消费。

分层：数据访问归 ``GraphStore.curation_facts``（确定性 SQL），本模块持有
阈值配置与议程语义（事实塑形 + 摘要行）。事实归系统、决策归 AI（对齐
技能 curator 范式）：议程只陈述事实（弱边 / 陈旧边 / 歧义关系对 / 疑似
重复节点 / 异常枢纽），AI 经 graph_curation_agenda 工具读取，用图谱工具
（graph_merge_nodes / graph_remove_relation / graph_update_relation）执行
治理，graph_curation 心跳任务定期消费议程并沉淀处置摘要。

Model Experience:
- 模型看到什么：仅经 graph_curation_agenda 工具按需取议程（心跳任务
  graph_curation 定期消费）；无议程零注入
- token 影响：工具返回有各类上限（弱边/陈旧/歧义/重复各 10 条、枢纽 5 个）
- 缓存影响：纯工具通道 + 心跳日志摘要，不触碰任何 prompt 前缀层
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any, Dict, List

from core.config import get_config_float, get_config_int, register_configs_safe
from core.log import log

from .store import format_triple

if TYPE_CHECKING:
    from .store import GraphStore


async def build_agenda(graph: "GraphStore") -> Dict[str, Any]:
    """产出图谱治理议程（纯事实，无策略）；构建失败返回空议程。"""
    stale_days = get_config_int("graph_curation_stale_days", 60)
    try:
        facts = await graph.curation_facts(
            weak_strength=get_config_float("graph_curation_weak_strength", 0.4),
            stale_cutoff_ns=time.time_ns() - int(stale_days * 86_400 * 1e9),
            ambiguity_min_strength=get_config_float("graph_curation_ambiguity_min_strength", 0.5),
            hub_degree=get_config_int("graph_curation_hub_degree", 30),
        )
    except Exception as exc:
        log(f"图谱治理议程构建失败: {exc}", "DEBUG", tag="记忆")
        return {}
    return {
        "weak_edges": [_edge_item(edge) for edge in facts["weak_edges"]],
        "stale_edges": [_edge_item(edge) for edge in facts["stale_edges"]],
        "ambiguous_groups": [
            {
                "subject": group["subject_label"] or group["subject_key"],
                "predicate": group["predicate"],
                "edges": [_edge_item(edge) for edge in group["edges"]],
            }
            for group in facts["ambiguous_groups"]
        ],
        "duplicate_nodes": facts["duplicate_nodes"],
        "hub_nodes": facts["hub_nodes"],
    }


def agenda_summary(agenda: Dict[str, Any]) -> str:
    """议程摘要一行（心跳日志用）；全部为空返回空串。"""
    parts: List[str] = []
    if agenda.get("weak_edges"):
        parts.append(f"弱边 {len(agenda['weak_edges'])}")
    if agenda.get("stale_edges"):
        parts.append(f"陈旧 {len(agenda['stale_edges'])}")
    if agenda.get("ambiguous_groups"):
        parts.append(f"歧义 {len(agenda['ambiguous_groups'])}")
    if agenda.get("duplicate_nodes"):
        parts.append(f"疑似重复 {len(agenda['duplicate_nodes'])}")
    if agenda.get("hub_nodes"):
        parts.append(f"枢纽异常 {len(agenda['hub_nodes'])}")
    return "，".join(parts)


def _edge_item(edge: Dict[str, Any]) -> Dict[str, Any]:
    """治理议程的边条目（紧凑三元组 + 判断所需字段）。"""
    return {
        "id": edge["id"],
        "triple": format_triple(edge),
        "strength": round(edge["strength"], 2),
        "evidence": str(edge.get("evidence") or "")[:120],
        "origin": str(edge.get("origin") or ""),
        "updated_days_ago": round((time.time() - edge.get("updated", 0)) / 86400, 1),
    }


# ------------------------------------------------------------------
# 配置注册
# ------------------------------------------------------------------

_CURATION_CONFIGS = {
    "memory/graph": {
        "graph_curation_weak_strength": {
            "description": "治理议程·弱边强度阈值（低于此值的活跃边进入议程供 AI 处置）",
            "default": 0.4,
            "advanced": True,
            "value_type": "range",
            "min": 0,
            "max": 1,
            "step": 0.05,
        },
        "graph_curation_stale_days": {
            "description": "治理议程·陈旧边判定天数（未访问未更新超过该时长进入议程）",
            "default": 60,
            "advanced": True,
            "unit": "天",
        },
        "graph_curation_ambiguity_min_strength": {
            "description": "治理议程·歧义关系对的成员边最低强度（同主语同谓词多对象）",
            "default": 0.5,
            "advanced": True,
            "value_type": "range",
            "min": 0,
            "max": 1,
            "step": 0.05,
        },
        "graph_curation_hub_degree": {
            "description": "治理议程·枢纽异常度数阈值（自由型节点超此度数进入议程）",
            "default": 30,
            "advanced": True,
            "unit": "条",
        },
    },
}

register_configs_safe(_CURATION_CONFIGS)
