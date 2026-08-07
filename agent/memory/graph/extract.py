"""心跳关系抽取：从对话材料中提炼结构化关系事实并落库到关系图谱。

职责切分：本模块提供抽取 prompt、LLM 输出解析（纯函数，可测）与候选落库；
LLM 调用由心跳引擎发起（复用画像分析的对话收集与 alias 合并）。
落库原则「宁可少不可错」：无证据候选一律丢弃，strength 反映置信度。
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any, Dict, List

from core.log import log

from .store import parse_node_key

if TYPE_CHECKING:
    from agent.messages.characters import EntityData
    from agent.mind.mind import Mind

_MAX_CANDIDATES = 10
_MAX_MATERIAL_CHARS = 6000
_JSON_ARRAY_RE = re.compile(r"\[.*\]", re.DOTALL)

_RELATION_EXTRACT_PROMPT = """\
请从以下对话记录中提取实体之间的结构化关系事实，输出 JSON 数组。

## 当前实体
{entity}（节点标识：{entity_key}）

## 输出格式（只输出 JSON 数组，不要输出任何其他内容）
[
  {{
    "subject": "主体节点标识",
    "predicate": "关系类型",
    "object": "客体节点标识",
    "symmetric": false,
    "strength": 0.7,
    "evidence": "证据摘要（哪句话/哪个事实得出的）",
    "subject_label": "主体称呼（可选）",
    "object_label": "客体称呼（可选）"
  }}
]

## 节点标识规则
- 当前实体：{entity_key}
- 对话中出现的其他用户：消息标签 [uid:xxx] 对应的标识为 user:{adapter}:xxx
- 无法确定 uid 的人物（只提到名字）：person:名字
- 事物/话题/作品等概念：topic:名称（如 topic:火锅）

## 关系类型参考
人物关系：家人/朋友/同事/同学/恋人/上下级/师生；通用关系：喜欢/讨厌/属于/参与/擅长/使用/位于。
对称关系（朋友/同事/同学等）symmetric 填 true；单向关系（喜欢/属于等）填 false。

## 抽取原则（必须遵守）
1. 只提取对话中有明确依据的关系，禁止推测；拿不准的一律不输出
2. evidence 必填且必须来自对话内容，无证据的关系不输出
3. strength 表示置信度：明确陈述 0.8-1.0，间接推断 0.5-0.7
4. 没有关系事实时输出空数组 []
5. 最多输出 {max_items} 条，优先重要关系

## 对话记录
{material}
"""


def parse_relation_candidates(raw: str, *, max_items: int = _MAX_CANDIDATES) -> List[Dict[str, Any]]:
    """解析 LLM 输出为关系候选列表（容错：截取首个 JSON 数组，逐项校验）。

    非法项静默丢弃；整体解析失败返回空列表（调用方按"本轮无产出"处理）。
    """
    text = (raw or "").strip()
    if not text:
        return []
    m = _JSON_ARRAY_RE.search(text)
    if not m:
        return []
    try:
        items = json.loads(m.group(0))
    except json.JSONDecodeError:
        return []
    if not isinstance(items, list):
        return []

    candidates: List[Dict[str, Any]] = []
    for item in items[:max_items]:
        if not isinstance(item, dict):
            continue
        subject = str(item.get("subject", "")).strip()
        predicate = str(item.get("predicate", "")).strip()
        obj = str(item.get("object", "")).strip()
        evidence = str(item.get("evidence", "")).strip()
        if not (subject and predicate and obj and evidence):
            continue
        try:
            parse_node_key(subject)
            parse_node_key(obj)
        except ValueError:
            continue
        try:
            strength = float(item.get("strength", 0.7))
        except (TypeError, ValueError):
            strength = 0.7
        candidates.append({
            "subject": subject,
            "predicate": predicate,
            "object": obj,
            "symmetric": bool(item.get("symmetric", False)),
            "strength": max(0.0, min(1.0, strength)),
            "evidence": evidence,
            "subject_label": str(item.get("subject_label", "")).strip(),
            "object_label": str(item.get("object_label", "")).strip(),
        })
    return candidates


def build_extract_prompt(entity_desc: str, entity_key: str, adapter: str, material: str) -> str:
    """组装关系抽取 prompt（对话材料按字符上限截断）。"""
    return _RELATION_EXTRACT_PROMPT.format(
        entity=entity_desc,
        entity_key=entity_key,
        adapter=adapter or "unknown",
        material=material[:_MAX_MATERIAL_CHARS],
        max_items=_MAX_CANDIDATES,
    )


def render_material(conversation: List[Dict[str, Any]]) -> str:
    """把对话记录渲染为抽取材料（保留 [uid:] 等实体标签，倒取最近若干条）。"""
    lines: List[str] = []
    for msg in conversation[-40:]:
        content = msg.get("content", "")
        if not isinstance(content, str) or not content.strip():
            continue
        role = "assistant" if msg.get("role") == "assistant" else "user"
        lines.append(f"[{role}] {content.strip()}")
    return "\n".join(lines)


async def extract_and_store_relations(
    mind: "Mind",
    entity: "EntityData",
    conversation: List[Dict[str, Any]],
) -> int:
    """从对话材料抽取关系并落库，返回新增/更新的边数（0 表示无产出或失败）。"""
    if not mind.memory_store:
        return 0
    identity_type, identity_id = entity.identity_parts
    entity_key = f"{identity_type}:{identity_id}"
    material = render_material(conversation)
    if len(material) < 30:
        return 0

    prompt = build_extract_prompt(
        entity.get_entity_desc(), entity_key, entity.adapter_key or "", material,
    )
    raw = await mind.reflect(
        [{"role": "user", "content": f"[系统任务 - relation_extract]\n{prompt}"}],
        options={"temperature": 0.2},
    )
    candidates = parse_relation_candidates(raw or "")
    if not candidates:
        return 0

    graph = mind.memory_store.graph
    stored = 0
    for cand in candidates:
        try:
            await graph.add_relation(
                cand["subject"], cand["predicate"], cand["object"],
                subject_label=cand["subject_label"], object_label=cand["object_label"],
                symmetric=cand["symmetric"], strength=cand["strength"],
                evidence=cand["evidence"], origin="heartbeat_extract",
            )
            stored += 1
        except ValueError as exc:
            log(f"关系候选落库跳过: {cand.get('predicate')} -> {exc}", "DEBUG", tag="心跳")
    if stored:
        log(f"关系抽取: {entity.get_entity_desc()} -> {stored} 条关系", tag="心跳")
    return stored
