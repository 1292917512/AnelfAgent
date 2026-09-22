"""内部记忆工具 — Agent 大脑的长期记忆接口。

记忆是 Agent 的核心认知能力，MemoryStore / Embedder 引用经
``memory_tools_port`` 晚绑定端口分发（工具 import 时注册、拿不到构造参数；
由 agent.runtime.wiring 统一施绑）。
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Dict, NamedTuple, Optional

from core.config import get_config_float, get_config_int
from core.entity import EntityRegistry
from core.latebind import LateBinding
from core.log import log
from core.tool_errors import ErrorCause, error_from_exception, tool_error
from entities._sdk import deferred_tool

from .embedding import Embedder, wake_embedding_worker
from .graph import entity_node_keys
from .hub import HUB_TAG
from .memory_store import MemoryStore
from .memory_types import MemoryEntry, MemorySearchResult, MemoryType
from .probe import deep_probe_hub
from .store.tag_intel import ASSOC_PREFIXES, ENTITY_PREFIXES

EntityRegistry.register_group_order("memory", 10)


class MemoryToolDeps(NamedTuple):
    """记忆工具组运行时依赖（wiring 一次性施绑）。"""

    store: MemoryStore
    embedder: Optional[Embedder]


#: 记忆工具组依赖端口（bootstrap 经 agent.runtime.wiring 施绑）
memory_tools_port: LateBinding[MemoryToolDeps] = LateBinding("memory.tools")


def _deps() -> Optional[MemoryToolDeps]:
    """取记忆工具依赖（端口未施绑时返回 None，工具降级为未就绪错误）。"""
    return memory_tools_port.get() if memory_tools_port.bound else None

_TYPE_MAP = {
    "trait": MemoryType.ENTITY,
    "event": MemoryType.EPISODIC,
    "fact": MemoryType.SEMANTIC,
    "reflection": MemoryType.REFLECTION,
    "entity": MemoryType.ENTITY,
    "permanent": MemoryType.PERMANENT,
}


def _store_not_ready() -> str:
    """记忆存储未就绪的统一错误。"""
    return tool_error(
        "记忆系统未初始化",
        cause=ErrorCause.STATE, retryable=False,
        hint="记忆组件未初始化，请检查服务启动状态",
    )


def _cognee_not_ready() -> str:
    """Cognee 后端未就绪的统一错误。"""
    return tool_error(
        "Cognee 运行时未初始化",
        cause=ErrorCause.STATE, retryable=False,
        hint="Cognee 为可选记忆后端，请确认其已启用并完成初始化",
    )


def _current_scope_tag() -> str:
    """解析当前对话 scope 为实体标签（user_X → user:X，group_X → group:X）。

    memorize 未指定实体标签时自动补充，让记忆自动挂入关联网络。
    """
    try:
        from agent.mind.tool_activation import ToolActivationManager
        scope = ToolActivationManager.current_scope()
        if scope.startswith("user_"):
            return f"user:{scope[5:]}"
        if scope.startswith("group_"):
            return f"group:{scope[6:]}"
    except Exception:
        pass  # 非工具会话上下文（心跳/后台路径）无 scope 属常态，返回空串
    return ""


# ------------------------------------------------------------------
# 工具实现
# ------------------------------------------------------------------

@deferred_tool(
    group="memory", tags=["always"], source="mind.memory", timeout=300.0,
    description="将一条关键信息存入长期记忆（内容简洁精炼；type:permanent 为永久记忆）。"
    "返回 verdict 落盘裁决，落盘纪律见「记忆体系铁律」。",
)
async def memorize(
    content: str,
    tags: str = "",
    importance: float = 0.7,
    sensitivity: str = "normal",
    temporal_scope: str = "",
    linked_to: str = "",
) -> str:
    """将一条关键信息存入长期记忆。

    Args:
        content: 要记住的内容（简洁扼要，一两句话）
        tags: 标签，逗号分隔。前缀：type:(fact/event/permanent) user:(uid) group:(id) topic:(主题) goal:(目标id)。
            与某目标相关的记忆打 goal:xxx 标签，可在目标视角串联召回
        importance: 重要性 0-1，按校准表取值：0.9+ 身份级事实（姓名/生日/住址/重要关系、
            用户明确说"记住这个"）；0.8 长期偏好与习惯、重要约定与承诺；0.7 阶段性计划
            与近期动态；0.6 一般偏好线索；0.5 弱线索。主体是已相识的人时身份/关系级从高档。
            permanent 类型自动设为 1.0
        sensitivity: 私密度。normal（默认）/ private（他人私事）/ secret（高度敏感）；
            私事参与召回照常，转述边界见「记忆体系铁律」
        temporal_scope: 时间语义，仅事件/状态类填写：state=持续状态（如"在上海出差"，
            超期自动按过去时转述并降权）/ episode=一次性事件 / pattern=反复模式（默认）。
            稳定事实（fact）不填
        linked_to: 关联记忆 ID，逗号分隔（可选，最多 5 条）。仅挂「本条纠正/补充/
            依赖哪条既有记忆」的具体关系（如纠错教训挂被纠正的旧条目）——recall 命中
            关联方时会一跳带出本条；同主题关联是标签共现的职责，不要用 linked_to

    Returns:
        JSON 字符串。verdict 为落盘裁决：stored（新写入）/ updated（更新既有记忆）/
        merged（多条合并）/ skipped_duplicate（重复，未写入）。
    """
    try:
        deps = _deps()
        if deps is None:
            return _store_not_ready()
        store = deps.store

        # 威胁扫描：记忆写入是注入持久化的关键路径，命中威胁模式时拒绝写入
        from agent.security.threat_scanner import first_threat_message, is_threat_scan_enabled
        if is_threat_scan_enabled():
            threat = first_threat_message(content, scope="strict")
            if threat:
                log(f"记忆写入被威胁扫描拦截: {threat}", "WARNING", tag="安全")
                return tool_error(
                    f"写入被拒绝：{threat}。请检查内容是否包含注入指令。",
                    cause=ErrorCause.PERMISSION, retryable=False,
                )

        tag_list = _normalize_tags(tags)

        # 自动关联：未指定实体标签时，用当前对话 scope 补充（记忆自动挂到关联网络）
        if not any(t.startswith(ENTITY_PREFIXES) for t in tag_list):
            scope_tag = _current_scope_tag()
            if scope_tag:
                tag_list.append(scope_tag)

        mem_type = MemoryType.SEMANTIC
        for t in tag_list:
            if t.startswith("type:"):
                mem_type = _TYPE_MAP.get(t.split(":", 1)[1], MemoryType.SEMANTIC)
                break

        if mem_type == MemoryType.PERMANENT:
            importance = 1.0
            return await _upsert_permanent(content, tag_list, importance)

        sensitivity = (sensitivity or "normal").strip().lower()
        if sensitivity not in ("normal", "private", "secret"):
            sensitivity = "normal"
        temporal_scope = (temporal_scope or "").strip().lower()
        if temporal_scope not in ("state", "episode", "pattern"):
            temporal_scope = ""
        link_ids = await _parse_linked_to(store, linked_to)

        # 第一级：规则判重（子串/字面高相似，零成本快速拦截）
        if await store.has_similar_content(content):
            from . import metrics
            metrics.incr("write.dedup_rule_skip")
            return json.dumps({
                "ok": False, "verdict": "skipped_duplicate",
                "message": "已存在相似记忆，跳过（未重复写入）",
            }, ensure_ascii=False)

        # 第二级：LLM 语义裁决（事实演进 update / 语义重复 skip / 无重复 store）
        from . import metrics
        from .dedup import apply_update, gather_dedup_candidates, judge_write
        candidates = await gather_dedup_candidates(store, deps.embedder, content)
        decision = await judge_write(content, candidates)
        action = decision.get("action", "store")
        metrics.incr(f"write.dedup_llm_{action}")
        if action == "skip":
            from .dedup import apply_evidence_signals
            await apply_evidence_signals(store, action, content, candidates)
            return json.dumps({
                "ok": False, "verdict": "skipped_duplicate",
                "message": f"已有等价记忆，跳过（{decision.get('reason', '语义重复')}）（未重复写入）",
            }, ensure_ascii=False)
        if action == "update" and decision.get("target_id"):
            updated = await apply_update(
                store, int(decision["target_id"]),
                str(decision.get("content") or content), tag_list,
                actor="tool:memorize",
            )
            if updated is not None:
                dirty = False
                if sensitivity != "normal":
                    updated.metadata["sensitivity"] = sensitivity
                    dirty = True
                if link_ids:
                    _merge_linked_to(updated.metadata, link_ids)
                    dirty = True
                if dirty:
                    await store.update(updated, actor="tool:memorize")
                from .dedup import apply_evidence_signals
                await apply_evidence_signals(
                    store, action, content, candidates,
                    target_ids=[updated.id] if updated.id else None,
                )
                wake_embedding_worker()
                return json.dumps({
                    "ok": True, "id": updated.id, "action": "updated", "verdict": "updated",
                    "message": "与既有记忆为同一事实，已合并更新",
                    "content_preview": updated.content[:100],
                }, ensure_ascii=False)
            # 目标已不存在等异常：回退为正常写入
        if action == "merge" and decision.get("target_ids"):
            merge_ids = [int(i) for i in decision["target_ids"]]
            merged_content = str(decision.get("content") or content)
            keep_id = await store.merge_memories(
                merge_ids, merged_content, actor="tool:memorize",
            )
            if keep_id:
                if link_ids:
                    keep_entry = await store.get(keep_id)
                    if keep_entry is not None:
                        _merge_linked_to(keep_entry.metadata, link_ids)
                        await store.update(keep_entry, actor="tool:memorize")
                from .dedup import apply_evidence_signals
                await apply_evidence_signals(
                    store, action, content, candidates, target_ids=[keep_id],
                )
                wake_embedding_worker()
                return json.dumps({
                    "ok": True, "id": keep_id, "action": "merged", "verdict": "merged",
                    "message": f"已与 {len(merge_ids)} 条既有记忆合并为一条",
                    "merged_from": merge_ids,
                }, ensure_ascii=False)
            # 合并目标失效：回退为正常写入

        entry = MemoryEntry(
            memory_type=mem_type,
            content=content,
            tags=tag_list,
            importance=max(0.0, min(1.0, importance)),
            metadata=({
                **({"sensitivity": sensitivity} if sensitivity != "normal" else {}),
                **({"temporal_scope": temporal_scope} if temporal_scope else {}),
                **({"linked_to": link_ids} if link_ids else {}),
            }),
        )
        from .reflection_lifecycle import seed_reflection
        seed_reflection(entry)

        # 近重复提示须在写入前计算（写入后新标签已进入统计，会被误判为既有标签）
        hints = await _tag_near_duplicate_hints(tag_list)
        mid = await store.add(entry, actor="tool:memorize")
        wake_embedding_worker()
        result: Dict[str, Any] = {"ok": True, "id": mid, "verdict": "stored", "tags": tag_list}
        if hints:
            result["tag_hints"] = hints
        return json.dumps(result, ensure_ascii=False)
    except Exception as e:
        return error_from_exception(e, action="写入记忆")


def _normalize_tags(tags: str) -> list[str]:
    """标签规范化：trim、全角逗号/冒号归一、折叠内部空白、去重保序。"""
    import re
    out: list[str] = []
    for raw in re.split(r"[,，]", tags):
        tag = re.sub(r"\s+", " ", raw.strip().replace("：", ":"))
        if tag and tag not in out:
            out.append(tag)
    return out


_LINKED_TO_CAP = 5


async def _parse_linked_to(store: Any, linked_to: str) -> list[int]:
    """解析 linked_to 参数为合法记忆 id 列表（存在性校验，上限 _LINKED_TO_CAP）。"""
    ids: list[int] = []
    for s in (linked_to or "").replace("，", ",").split(","):
        s = s.strip().lstrip("#")
        if s.isdigit() and int(s) not in ids:
            ids.append(int(s))
    valid: list[int] = []
    for i in ids[:_LINKED_TO_CAP]:
        if await store.get(i) is not None:
            valid.append(i)
    return valid


def _merge_linked_to(metadata: Dict[str, Any], ids: list[int]) -> None:
    """把 linked_to 目标 id 合入条目 metadata（追加去重，上限 _LINKED_TO_CAP）。"""
    if not ids:
        return
    existing = [i for i in metadata.get("linked_to", []) if isinstance(i, int)]
    metadata["linked_to"] = list(dict.fromkeys(existing + ids))[:_LINKED_TO_CAP]


async def _tag_near_duplicate_hints(tag_list: list[str]) -> list[str]:
    """新 topic: 标签与既有高频标签存在包含关系时给出归并建议（只提示不改数据）。"""
    new_topics = [t for t in tag_list if t.startswith("topic:")]
    deps = _deps()
    if not new_topics or deps is None:
        return []
    try:
        tag_df = await deps.store.list_tags()
    except Exception:
        return []
    hints: list[str] = []
    for tag in new_topics:
        if tag in tag_df:
            continue  # 已存在的标签不算新
        name = tag[len("topic:"):]
        for existing, count in sorted(tag_df.items(), key=lambda kv: -kv[1]):
            if not existing.startswith("topic:") or count < 2:
                continue
            other = existing[len("topic:"):]
            if name and other and (name in other or other in name):
                hints.append(f"新标签 {tag} 与既有 {existing}（{count} 次）相近，建议统一用后者")
                break
        if len(hints) >= 3:
            break
    return hints


async def _upsert_permanent(content: str, tag_list: list[str], importance: float) -> str:
    """永久记忆的 upsert：按非 type: 标签匹配已有条目，存在则更新，不存在则新增。

    主标签记忆（main:hub）只按 HUB_TAG 单标签匹配——AI 覆写时可自由附带
    其他标签，交集匹配会因此失配而创建出重复 hub。
    """
    deps = _deps()
    assert deps is not None  # 调用方 memorize 已做未就绪检查
    store = deps.store
    match_tags = [t for t in tag_list if not t.startswith("type:")]
    if HUB_TAG in match_tags:
        match_tags = [HUB_TAG]
    existing: list[MemoryEntry] = []
    if match_tags:
        candidates = await store.search_by_tags(match_tags, limit=10)
        existing = [e for e in candidates if e.memory_type == MemoryType.PERMANENT]

    if existing:
        target = existing[0]
        old_preview = target.content[:60]
        target.content = content
        target.tags = tag_list
        target.importance = importance
        # 内容已变更，清空旧向量，由后台 worker 重新生成
        target.embedding = None
        await store.update(target, clear_embedding=True, actor="tool:memorize")
        wake_embedding_worker()
        return json.dumps({
            "ok": True, "id": target.id, "action": "updated", "verdict": "updated",
            "old_preview": old_preview, "tags": tag_list,
        }, ensure_ascii=False)

    entry = MemoryEntry(
        memory_type=MemoryType.PERMANENT,
        content=content,
        tags=tag_list,
        importance=importance,
    )
    mid = await store.add(entry, actor="tool:memorize")
    wake_embedding_worker()
    return json.dumps({
        "ok": True, "id": mid, "action": "created", "verdict": "stored", "tags": tag_list,
    }, ensure_ascii=False)


@deferred_tool(
    group="memory", tags=["always"], source="mind.memory",
    description="在长期记忆中语义搜索，返回最相关的记忆及其联想关联。"
    "结果带 source 出处（memory=数据库 / file=便签 / cognee_graph|cognee_chunk=知识图谱）"
    "与归属标注（uid/group 语义及引用纪律见「记忆体系铁律」）；"
    "返回的 forgotten 字段是已遗忘的记忆（强相关或常规检索无果时附带）："
    "kind=archived 的可经 restore_memory(id) 恢复，kind=tombstone 的仅剩梗概需重新 memorize。",
)
async def recall(
    query: str,
    tags: str = "",
    limit: int = 5,
    min_score: float = 0.0,
    depth: str = "shallow",
    filter_tags: str = "",
) -> str:
    """在长期记忆中语义搜索（同时检索 memories 表和 MD 文件索引）。

    Args:
        query: 搜索查询（自然语言）
        tags: 可选标签加权，逗号分隔（如 user:123），命中加分但不过滤
        limit: 最大返回数量，默认 5
        min_score: 最低相关度过滤（0-1，相对最高分归一化）。默认 0 不过滤；
            需要精确模式减少噪音时建议 0.5~0.7，只保留高相关记忆
        depth: 召回深度。shallow（默认）快速混合检索；deep 追加知识图谱检索、
            扩大候选池并做二跳标签联想，覆盖更全但更慢，浅召回找不到时再用
        filter_tags: 硬过滤标签，逗号分隔。结果必须包含全部指定标签；
            启用后只返回数据库记忆（文件便签无标签体系）
    """
    try:
        deps = _deps()
        if deps is None:
            return _store_not_ready()
        store = deps.store

        tag_list = _normalize_tags(tags) or None
        hard_tags = _normalize_tags(filter_tags) or None
        is_deep = depth.strip().lower() == "deep"

        # 查询提及识别：查询文本中提到的已知实体/话题自动转为标签加权
        mention_tags = await store.extract_query_mentions(query)
        if mention_tags:
            tag_list = list(dict.fromkeys((tag_list or []) + mention_tags))

        query_vec = None
        if deps.embedder:
            query_vec = await deps.embedder.embed_query(query)

        from .cognee.config import load_cognee_config
        from .cognee.fusion import federated_search
        from .cognee.runtime import get_cognee_client
        cognee_config = load_cognee_config()
        entity_scope = ""
        for tag in tag_list or []:
            if tag.startswith(ENTITY_PREFIXES):
                scope_type, scope_id = tag.split(":", 1)
                entity_scope = f"{scope_type}_{scope_id}"
                break
        pool_multiplier = cognee_config.recall_pool_multiplier * (2 if is_deep else 1)
        # 实体标签 → 图谱节点：邻域查询（relations 字段）与 cognee node_name
        # 定向检索共用——聊天对象明确时定向检索精度远高于泛化语义检索
        node_keys = entity_node_keys(tag_list, entity_scope, adapter=_current_scope_adapter())
        node_names: list[str] = []
        if node_keys:
            try:
                node_names = await store.graph.labels_for_keys(node_keys)
            except Exception:
                node_names = []
        # 遗忘层兜底与主检索并行：归档（可恢复）+ 墓碑（仅痕迹）统一打分
        results, forgotten = await asyncio.gather(
            federated_search(
                store.search_unified(
                    query=query,
                    query_vec=query_vec,
                    query_tags=tag_list,
                    limit=limit * pool_multiplier,
                    require_tags=hard_tags,
                ),
                query=query,
                client=get_cognee_client(),
                config=cognee_config,
                limit=limit,
                entity_scope=entity_scope,
                query_tags=tag_list,
                deep=is_deep,
                node_names=node_names or None,
            ),
            store.search_forgotten(
                query, query_vec,
                limit=get_config_int("memory_forgotten_recall_limit", 3),
            ),
        )

        if min_score > 0:
            results = [r for r in results if r.score >= min_score]

        mem_ids = [
            int(r.id.split(":")[1])
            for r in results
            if r.source == "memory" and r.id.startswith("mem:")
        ]
        if mem_ids:
            await store.record_access(mem_ids)

        # 主动检索入召回账本：同一回复内异步深探不重复注入 AI 已取回的内容
        #（scope 未知时广播到全部活跃账本，防与回复账本隔离）
        try:
            deep_probe_hub.record(entity_scope, results=results)
        except Exception:
            pass

        items = [{
            "id": r.id,
            "source": r.source,
            "content": r.snippet[:300],
            "type": r.memory_type or "",
            "tags": r.tags,
            "score": round(r.score, 3),
            **({"time": time.strftime("%Y-%m-%d %H:%M", time.localtime(r.timestamp))} if r.timestamp else {}),
            **({"sensitivity": r.sensitivity} if r.sensitivity != "normal" else {}),
            **({"path": r.path} if r.source == "file" else {}),
            **({"dataset": r.dataset_name} if r.dataset_name else {}),
        } for r in results]

        # 遗忘层采纳规则：向量强匹配（≥ 配置阈值）随时浮现；
        # 关键词弱命中只在常规检索无果时出现（"似曾相识"而非干扰）
        archive_min = get_config_float("memory_archive_recall_min_score", 0.5)
        main_empty = not results
        forgotten_items = [
            item for item in forgotten
            if item["score"] >= archive_min or (main_empty and item["score"] >= 0.25)
        ]
        forgotten_out = [_format_forgotten_item(item) for item in forgotten_items]

        # 关联扩展：沿标签网络联想相关记忆（想到一件事 → 唤起相关的事）
        if is_deep:
            related_items = await _recall_associations_deep(results, mem_ids)
        else:
            related_items = await _recall_associations(results, mem_ids)

        # 深度召回追加关系网络邻域：查询涉及的实体在图谱中的已知关系
        relations: list[str] = []
        if is_deep and node_keys:
            from .graph import format_triple
            edges = await store.graph.edges_for_scopes(node_keys, limit=10)
            relations = [format_triple(e) for e in edges]
            # 工具返回的关系边同样入账本：异步深探不重复注入 AI 已取回的关系
            deep_probe_hub.record(
                entity_scope, edge_ids=(int(e["id"]) for e in edges),
            )

        return json.dumps({
            "count": len(items),
            "depth": "deep" if is_deep else "shallow",
            "results": items,
            "related": related_items,
            **({"relations": relations} if relations else {}),
            **({
                "forgotten": forgotten_out,
                "forgotten_hint": "以下为已遗忘的记忆，不参与常规召回。"
                "kind=archived 的可经 restore_memory(id) 恢复到活跃记忆库；"
                "kind=tombstone 的原文已物理删除仅剩梗概，如需找回请基于梗概重新 memorize。",
            } if forgotten_out else {}),
        }, ensure_ascii=False)
    except Exception as e:
        return error_from_exception(e, action="搜索记忆")


def _format_forgotten_item(item: Dict[str, Any]) -> Dict[str, Any]:
    """遗忘层检索项 → 工具输出格式（归档可恢复 / 墓碑仅梗概）。"""
    base: Dict[str, Any] = {
        "kind": item["kind"],
        "type": item["type"],
        "tags": item["tags"],
        "score": round(float(item["score"]), 3),
        "reason": item["reason"],
    }
    if item["kind"] == "archived":
        return {
            "id": item["id"],
            **base,
            "content": str(item["content"])[:300],
            "archived_at": time.strftime("%Y-%m-%d", time.localtime(item["archived_at"])),
            "restorable": True,
        }
    return {
        "id": item["id"],
        **base,
        "gist": item["gist"],
        "purged_at": time.strftime("%Y-%m-%d", time.localtime(item["purged_at"])),
        "restorable": False,
    }


async def _recall_associations(
        results: list[MemorySearchResult],
        existing_mem_ids: list[int],
        *,
        max_related: int = 3,
) -> list[Dict[str, Any]]:
    """沿主结果的关联网络联想相关记忆（linked_to 显式链接优先，标签共现补充）。"""
    deps = _deps()
    if deps is None or not results:
        return []
    store = deps.store

    out: list[Dict[str, Any]] = []
    seen = set(existing_mem_ids)
    # linked_to 一跳带出：主结果条目显式声明的「纠正/补充/依赖」目标——
    # 强于统计联想的语义关联（纠错记忆挂在被纠正条目上，命中即带出），优先占位
    for mid in existing_mem_ids[:5]:
        if len(out) >= max_related:
            break
        source_entry = await store.get(mid)
        if source_entry is None:
            continue
        for lid in [i for i in source_entry.metadata.get("linked_to", []) if isinstance(i, int)]:
            if len(out) >= max_related:
                break
            if lid in seen:
                continue
            target = await store.get(lid)
            if target is None or target.importance <= 0 or target.id is None:
                continue
            seen.add(lid)
            out.append({
                "id": f"mem:{target.id}",
                "source": "memory",
                "content": target.content[:300],
                "type": target.memory_type.value,
                "tags": target.tags,
                "score": 0.9,
                "related": True,
                "hop": "link",
                "linked_from": mid,
            })

    assoc_tags: list[str] = []
    for r in results:
        for tag in r.tags:
            if tag.startswith(ASSOC_PREFIXES) and tag not in assoc_tags:
                assoc_tags.append(tag)
    if assoc_tags and len(out) < max_related:
        # 种子三层扩展（图谱邻居 + 标签共现）
        assoc_tags = await store.expand_tag_seeds(assoc_tags)
        related = await store.search_associative(
            assoc_tags, exclude_ids=seen, limit=max_related - len(out),
        )
        out.extend([
            {
                "id": f"mem:{entry.id}",
                "source": "memory",
                "content": entry.content[:300],
                "type": entry.memory_type.value,
                "tags": entry.tags,
                "score": round(score, 3),
                "related": True,
            }
            for entry, score in related
        ])
    return out


async def _recall_associations_deep(
        results: list[MemorySearchResult],
        existing_mem_ids: list[int],
        *,
        max_related: int = 6,
) -> list[Dict[str, Any]]:
    """深度联想：在一跳基础上再做二跳扩展（联想链），第二跳分数打 0.75 折。

    一跳种子除主结果标签外，还会从 cognee 高分命中（score ≥ 0.5）的
    provenance.anelf_memory_id 回库取对应记忆的标签——图谱先找到方向，
    再回向量库深潜提取相邻记忆。
    """
    seed_results = results
    deps = _deps()
    store = deps.store if deps else None
    if store is not None:
        extra_tags: list[str] = []
        for r in results:
            if not r.source.startswith("cognee") or r.score < 0.5:
                continue
            mem_id = r.provenance.get("anelf_memory_id")
            if not mem_id:
                continue
            try:
                entry = await store.get(int(mem_id))
            except (TypeError, ValueError):
                continue
            if entry:
                for tag in entry.tags:
                    if tag.startswith(ASSOC_PREFIXES) and tag not in extra_tags:
                        extra_tags.append(tag)
        if extra_tags:
            seed_results = list(results) + [
                MemorySearchResult(id="seed", snippet="", score=0.0, tags=extra_tags)
            ]

    hop1 = await _recall_associations(seed_results, existing_mem_ids, max_related=max_related)
    if store is None or len(hop1) >= max_related:
        return hop1

    seen_ids = set(existing_mem_ids)
    hop1_tags: list[str] = []
    for item in hop1:
        mem_id = int(str(item["id"]).split(":")[1])
        seen_ids.add(mem_id)
        for tag in item["tags"]:
            if tag.startswith(ASSOC_PREFIXES) and tag not in hop1_tags:
                hop1_tags.append(tag)
    if not hop1_tags:
        return hop1

    hop2 = await store.search_associative(
        hop1_tags, exclude_ids=seen_ids, limit=max_related - len(hop1),
    )
    for entry, score in hop2:
        hop1.append({
            "id": f"mem:{entry.id}",
            "source": "memory",
            "content": entry.content[:300],
            "type": entry.memory_type.value,
            "tags": entry.tags,
            "score": round(score * 0.75, 3),
            "related": True,
            "hop": 2,
        })
    return hop1


@deferred_tool(
    group="memory", tags=["core", "heartbeat"], source="mind.memory",
    description="浏览记忆索引。不传 tag 返回标签统计；传 tag 返回该标签下的记忆列表。",
)
async def memory_index(tag: str = "") -> str:
    """浏览记忆索引（包含 memories 表和文件索引统计）。

    Args:
        tag: 可选，指定标签查看其下的记忆（如 user:123）
    """
    try:
        deps = _deps()
        if deps is None:
            return _store_not_ready()
        store = deps.store

        if not tag:
            tag_counts = await store.list_tags()
            total = await store.count()
            index_status = await store.get_index_status()
            merge_candidates = await store.tag_merge_candidates(limit=20)
            return json.dumps({
                "total_memories": total,
                "tags": tag_counts,
                "index": index_status,
                **({
                    "tag_merge_candidates": merge_candidates,
                    "tag_merge_hint": "标签归并候选（事实归系统、决策归你）：逐对确认后"
                    "经 update_memory 把 from 标签的记忆改挂 into 标签；确认全部处理后标签空间收敛",
                } if merge_candidates else {}),
            }, ensure_ascii=False)

        entries = await store.search_by_tags([tag], limit=20)
        items = [{
            "id": e.id,
            "summary": e.content[:80],
            "tags": e.tags,
            "importance": round(e.importance, 2),
        } for e in entries]
        return json.dumps({"tag": tag, "count": len(items), "memories": items}, ensure_ascii=False)
    except Exception as e:
        return error_from_exception(e, action="读取记忆索引")


@deferred_tool(
    group="memory", tags=["core", "heartbeat"], source="mind.memory",
    description="按 ID 获取一条记忆的完整内容、标签、变更史与合并去向。"
    "被合并的 id 自动重定向到存活条目；归档/物删的 id 返回分层语义。用于在修改前先确认记忆的当前状态。",
)
async def get_memory(memory_id: int) -> str:
    """按 ID 获取一条记忆的完整信息。

    Args:
        memory_id: 记忆 ID（从 recall / memory_index / memory_deep_search 结果中获取）
    """
    try:
        deps = _deps()
        if deps is None:
            return _store_not_ready()
        store = deps.store
        resolution = await store.resolve(memory_id)
        status = resolution["status"]
        chain: list[int] = list(resolution.get("chain") or [])
        if status == "missing":
            return tool_error(f"记忆 {memory_id} 不存在", cause=ErrorCause.NOT_FOUND, retryable=False)
        if status == "archived":
            row = resolution["row"]
            return json.dumps({
                "ok": True, "status": "archived", "id": row["id"],
                "content": row["content"][:300], "type": row["type"], "tags": row["tags"],
                "archived_at": time.strftime("%Y-%m-%d", time.localtime(row["archived_at"])),
                "reason": row["reason"],
                **({"redirect_chain": chain} if chain else {}),
                "hint": "已归档（不参与召回）；可 restore_memory 恢复到活跃记忆库",
            }, ensure_ascii=False)
        if status == "tombstone":
            row = resolution["row"]
            redirect = row.get("redirect_to")
            return json.dumps({
                "ok": True, "status": "tombstone", "id": row["memory_id"],
                "gist": row["gist"], "type": row["type"], "tags": row["tags"],
                "purged_at": time.strftime("%Y-%m-%d", time.localtime(row["purged_at"])),
                **({"redirect_chain": chain} if chain else {}),
                **({"redirect_to": redirect} if redirect else {}),
                "hint": (
                    f"原文已物理删除仅余梗概；内容已并入 #{redirect}，可 get_memory 查看"
                    if redirect
                    else "原文已物理删除仅余梗概；如需找回请基于梗概重新 memorize"
                ),
            }, ensure_ascii=False)
        entry = resolution.get("entry")
        if entry is None:
            return tool_error(
                f"记忆 {memory_id} 的并入链过深（{' → '.join(map(str, chain))}），无法解析到存活条目",
                cause=ErrorCause.NOT_FOUND, retryable=False,
                hint="请对链上较新的 id 调用",
            )
        result: Dict[str, Any] = {
            "ok": True, "status": "active",
            "id": entry.id,
            "type": entry.memory_type.value,
            "content": entry.content,
            "tags": entry.tags,
            "importance": round(entry.importance, 3),
            "source": entry.source,
            "access_count": entry.access_count,
            "version": entry.version,
            "sensitivity": entry.metadata.get("sensitivity", "normal"),
        }
        if chain:
            result["redirect_chain"] = chain
            result["note"] = f"#{chain[0]} 已并入本条（合并谱系），本条为其存活条目"
        linked = [i for i in entry.metadata.get("linked_to", []) if isinstance(i, int)]
        if linked:
            result["linked_to"] = linked
        merged_from = [i for i in entry.metadata.get("merged_from", []) if isinstance(i, int)]
        if merged_from:
            result["merged_from"] = merged_from
        audit = await store.list_audit(memory_id=entry.id, limit=5)
        if audit:
            result["audit"] = [
                {
                    "action": a["action"], "actor": a.get("actor") or "",
                    "time": time.strftime("%m-%d %H:%M", time.localtime(a["timestamp"])),
                    **({"detail": a["detail"][:60]} if a["detail"] else {}),
                }
                for a in audit
            ]
        return json.dumps(result, ensure_ascii=False)
    except Exception as e:
        return error_from_exception(e, action="读取记忆")


async def _resolve_active_for_mutation(
    store: Any, memory_id: int, action: str,
) -> tuple[Optional[Any], Optional[str]]:
    """变更类操作的 id 预检：仅活跃条目可变更，其余状态返回引导错误。

    被合并的僵尸条目（importance=0 + merged_into）直接改写会让变更落在
    已退出召回的尸体上——引导到存活条目；归档先恢复；墓碑与缺失如实告知。
    """
    resolution = await store.resolve(memory_id)
    status = resolution["status"]
    if status == "active":
        return resolution["entry"], None
    chain = list(resolution.get("chain") or [])
    if status == "merged":
        keep = resolution.get("entry")
        if keep is not None:
            return None, tool_error(
                f"记忆 {memory_id} 已并入 #{keep.id}（合并谱系），本身已退出召回",
                cause=ErrorCause.STATE, retryable=False,
                hint=f"请对 #{keep.id} 进行{action}",
            )
        return None, tool_error(
            f"记忆 {memory_id} 的并入链过深（{' → '.join(map(str, chain))}），无法解析",
            cause=ErrorCause.NOT_FOUND, retryable=False,
        )
    if status == "archived":
        return None, tool_error(
            f"记忆 {memory_id} 已归档（{resolution['row']['reason']}）",
            cause=ErrorCause.STATE, retryable=False,
            hint=f"先 restore_memory 恢复，再进行{action}",
        )
    if status == "tombstone":
        return None, tool_error(
            f"记忆 {memory_id} 已物理删除，仅余梗概",
            cause=ErrorCause.NOT_FOUND, retryable=False,
            hint="如需保留内容请基于梗概重新 memorize",
        )
    return None, tool_error(f"记忆 {memory_id} 不存在", cause=ErrorCause.NOT_FOUND, retryable=False)


@deferred_tool(
    group="memory", tags=["core", "heartbeat"], source="mind.memory",
    description=(
        "更新指定 ID 的记忆内容、标签或重要性。用于纠正错误记忆、补充细节或调整分类。"
        "建议先用 get_memory 查看当前内容再修改，至少提供 content / tags / importance 之一。"
    ),
)
async def update_memory(
    memory_id: int,
    content: str = "",
    tags: str = "",
    importance: float = -1.0,
    sensitivity: str = "",
) -> str:
    """原地更新一条记忆（保留 id 和创建时间戳）。

    Args:
        memory_id: 要更新的记忆 ID
        content: 新的记忆内容（留空则保持原内容不变）
        tags: 新的标签，逗号分隔（留空则保持原标签不变）
        importance: 新的重要性 0-1（传 -1 则保持原值不变）
        sensitivity: 新的私密度 normal/private/secret（留空则保持原值不变）
    """
    try:
        deps = _deps()
        if deps is None:
            return _store_not_ready()

        entry, err = await _resolve_active_for_mutation(deps.store, memory_id, "更新")
        if err:
            return err
        assert entry is not None

        changed: list[str] = []
        content_changed = False

        if content.strip() and content.strip() != entry.content:
            entry.content = content.strip()
            changed.append("content")
            content_changed = True

        if tags.strip():
            new_tags = _normalize_tags(tags)
            if new_tags != entry.tags:
                entry.tags = new_tags
                changed.append("tags")

        if 0.0 <= importance <= 1.0 and round(importance, 3) != round(entry.importance, 3):
            entry.importance = importance
            changed.append("importance")

        if sensitivity.strip():
            new_sens = sensitivity.strip().lower()
            if new_sens in ("normal", "private", "secret"):
                old_sens = entry.metadata.get("sensitivity", "normal")
                if new_sens != old_sens:
                    if new_sens == "normal":
                        entry.metadata.pop("sensitivity", None)
                    else:
                        entry.metadata["sensitivity"] = new_sens
                    changed.append("sensitivity")

        if not changed:
            return json.dumps({"ok": True, "message": "无变更"}, ensure_ascii=False)

        # 内容变更时清空旧向量，由后台 worker 重新生成
        if content_changed:
            entry.embedding = None

        ok = await deps.store.update(entry, clear_embedding=content_changed, actor="tool:update_memory")
        if ok and content_changed:
            wake_embedding_worker()
        return json.dumps({
            "ok": ok,
            "id": memory_id,
            "changed": changed,
            "content_preview": entry.content[:100],
            "tags": entry.tags,
            "importance": round(entry.importance, 3),
        }, ensure_ascii=False)
    except Exception as e:
        return error_from_exception(e, action="更新记忆")


@deferred_tool(group="memory", tags=["core", "heartbeat"], source="mind.memory")
async def forget(memory_id: int) -> str:
    """遗忘指定 ID 的记忆（软删除：移入归档，不参与召回，可由系统恢复）。

    Args:
        memory_id: 要遗忘的记忆 ID
    """
    try:
        deps = _deps()
        if deps is None:
            return _store_not_ready()
        entry, err = await _resolve_active_for_mutation(deps.store, memory_id, "遗忘")
        if err:
            return err
        if entry is not None and HUB_TAG in entry.tags:
            return tool_error(
                "主标签记忆（main:hub）是系统常驻的索引中枢，禁止归档",
                cause=ErrorCause.PERMISSION, retryable=False,
                hint="需要更新其内容时，用 memorize 携带 type:permanent + main:hub 标签整段覆写",
            )
        ok = await deps.store.archive_memory(memory_id, reason="manual_forget", actor="tool:forget")
        return json.dumps({
            "ok": ok,
            "message": f"记忆 {memory_id} {'已遗忘（归档，可恢复）' if ok else '不存在'}",
        }, ensure_ascii=False)
    except Exception as e:
        return error_from_exception(e, action="遗忘记忆")


@deferred_tool(
    group="memory", tags=["always"], source="mind.memory",
    description="把一条已遗忘（归档）的记忆恢复到活跃记忆库，恢复后正常参与召回。"
    "仅当 recall 返回的 forgotten 列表中 kind=archived 且确认内容确有需要时使用。",
)
async def restore_memory(memory_id: int) -> str:
    """从归档恢复记忆（向量与访问记录原样回填，无需重新嵌入）。

    Args:
        memory_id: 归档记忆的 ID（recall 返回的 forgotten 列表中 kind=archived 项的 id）
    """
    try:
        deps = _deps()
        if deps is None:
            return _store_not_ready()
        ok = await deps.store.restore_memory(memory_id, actor="tool:restore")
        if not ok:
            return tool_error(
                f"归档中不存在记忆 #{memory_id}",
                cause=ErrorCause.NOT_FOUND, retryable=False,
                hint="该记忆可能已被恢复，或已超归档保留期被物理删除"
                "（forgotten 列表中 kind=tombstone 的项仅剩梗概，请基于梗概重新 memorize）",
            )
        return json.dumps({
            "ok": True, "id": memory_id, "action": "restored",
        }, ensure_ascii=False)
    except Exception as e:
        return error_from_exception(e, action="恢复记忆")


# ------------------------------------------------------------------
# 跨频道会话管理
# ------------------------------------------------------------------











def _get_sqlite():
    from agent.runtime.singleton import require_runtime
    return require_runtime().data_center.sqlite


def _normalize_scope_id(scope_id: str) -> str:
    """归一化 LLM 传入的 scope_id：裸 id 自动补当前会话的 adapter 前缀。

    scope 新格式为 ``{adapter}:{base_id}``（如 ``qq:123``）。AI 从消息标签
    （``[uid:123]``）取到的往往是裸 id，按新键直接查询会 miss——按当前思维
    会话的 adapter 补全；已带前缀、含 ``#`` 子会话或当前无 scope（心跳/后台）
    时原样返回。
    """
    sid = (scope_id or "").strip()
    if not sid or ":" in sid.split("#", 1)[0]:
        return sid
    adapter = _current_scope_adapter()
    return f"{adapter}:{sid}" if adapter else sid


def _current_scope_adapter() -> str:
    """当前思维会话的频道 adapter（无会话上下文时为空串）。"""
    try:
        from agent.messages import parse_entity_scope
        from agent.mind.tool_activation import ToolActivationManager
        _st, adapter, _base, _sess = parse_entity_scope(ToolActivationManager.current_scope())
        return adapter or ""
    except Exception:
        return ""




















# ------------------------------------------------------------------
# 记忆统计、深度搜索、合并
# ------------------------------------------------------------------

@deferred_tool(
    group="memory", tags=["core", "heartbeat"], source="mind.memory",
    description="查看记忆系统统计和健康状态。返回各类型记忆数量、阈值预警、索引状态、"
    "运行指标（召回通道/写入去重累计计数）与近 24h 变更审计等信息。",
)
async def memory_stats() -> str:
    """查看记忆系统统计和健康状态。

    心跳状态区块只注入 AI 可行动项，本工具是遥测明细的唯一查询面。
    """
    try:
        deps = _deps()
        if deps is None:
            return _store_not_ready()
        health = await deps.store.get_health_status()
        from . import metrics as memory_metrics
        if memory_metrics.snapshot():
            health["metrics"] = memory_metrics.snapshot()
        audit = await deps.store.get_audit_summary(hours=24)
        if audit:
            health["recent_changes_24h"] = audit
        return json.dumps(health, ensure_ascii=False)
    except Exception as e:
        return error_from_exception(e, action="读取记忆统计")


@deferred_tool(
    group="memory", tags=["core", "heartbeat"], source="mind.memory",
    description="查看 Cognee 知识图谱记忆后端的安装、可用性、同步积压和失败状态。",
)
async def cognee_status() -> str:
    """查看 Cognee 可选后端状态。"""
    try:
        from .cognee.config import load_cognee_config
        from .cognee.runtime import get_cognee_client, get_cognee_coordinator

        config = load_cognee_config()
        client = get_cognee_client()
        coordinator = get_cognee_coordinator()
        availability = (
            client.availability().model_dump()
            if client
            else {
                "installed": False, "enabled": config.enabled, "ready": False,
                "version": "", "reason": "运行时未初始化",
            }
        )
        sync = (
            (await coordinator.status()).model_dump()
            if coordinator
            else {"enabled": False, "running": False, "pending": 0, "failed": 0, "synced": 0}
        )
        return json.dumps(
            {"availability": availability, "sync": sync},
            ensure_ascii=False,
        )
    except Exception as e:
        return error_from_exception(e, action="查询 Cognee 状态")


@deferred_tool(
    group="memory", tags=["core", "heartbeat"], source="mind.memory",
    description="重试 Cognee 同步队列中已达到失败上限的记忆投影任务。",
)
async def retry_cognee_sync() -> str:
    """重试 Cognee 失败同步项。"""
    try:
        from .cognee.runtime import get_cognee_coordinator
        coordinator = get_cognee_coordinator()
        if not coordinator:
            return _cognee_not_ready()
        count = await coordinator.retry_failed()
        return json.dumps({"ok": True, "retried": count}, ensure_ascii=False)
    except Exception as e:
        return error_from_exception(e, action="重试 Cognee 同步")


@deferred_tool(
    group="memory", tags=["core"], source="mind.memory",
    description="列出当前 Cognee 可访问的数据集，仅用于诊断记忆作用域。",
)
async def list_cognee_datasets() -> str:
    """列出 Cognee datasets。"""
    try:
        from .cognee.runtime import get_cognee_client
        client = get_cognee_client()
        if not client:
            return _cognee_not_ready()
        values = await client.list_datasets()
        items = [
            value.model_dump(mode="json")
            if hasattr(value, "model_dump")
            else value if isinstance(value, dict)
            else {"id": str(getattr(value, "id", "")), "name": str(getattr(value, "name", ""))}
            for value in values
        ]
        return json.dumps({"datasets": items, "count": len(items)}, ensure_ascii=False)
    except Exception as e:
        return error_from_exception(e, action="列出 Cognee 数据集")


@deferred_tool(
    group="memory", tags=["core", "heartbeat"], source="mind.memory",
    description="显式运行指定 Cognee 数据集的知识图谱增强。仅对已确认存在的数据集使用。",
)
async def improve_cognee_dataset(dataset_name: str) -> str:
    """增强指定 Cognee 数据集。"""
    try:
        from .cognee.runtime import get_cognee_coordinator
        coordinator = get_cognee_coordinator()
        if not coordinator:
            return _cognee_not_ready()
        result = await coordinator.improve(dataset_name)
        value = result.model_dump(mode="json") if hasattr(result, "model_dump") else result
        return json.dumps({"ok": True, "result": value}, ensure_ascii=False, default=str)
    except Exception as e:
        return error_from_exception(e, action="增强 Cognee 数据集")


@deferred_tool(
    group="memory", tags=["core", "heartbeat"], source="mind.memory",
    description="压缩 Cognee 向量库存储，回收删除/更新遗留的历史版本占用的磁盘空间。"
    "不影响任何记忆数据（最新版本永远保留）。同步进行中时会排队到空闲窗口执行。",
)
async def compact_cognee_storage() -> str:
    """压缩 Cognee LanceDB 存储，回收磁盘空间。"""
    try:
        from .cognee.runtime import get_cognee_coordinator
        coordinator = get_cognee_coordinator()
        if not coordinator:
            return _cognee_not_ready()
        result = await coordinator.request_compact()
        return json.dumps(result, ensure_ascii=False, default=str)
    except Exception as e:
        return error_from_exception(e, action="压缩 Cognee 存储")


@deferred_tool(
    group="memory", tags=["core", "heartbeat"], source="mind.memory",
    description="分页深度搜索所有记忆，支持按类型过滤。用于整理和合并记忆时分批查看所有记忆。",
)
async def memory_deep_search(page: int = 1, page_size: int = 20, memory_type: str = "") -> str:
    """分页深度搜索所有记忆。

    Args:
        page: 页码，从 1 开始
        page_size: 每页数量，默认 20
        memory_type: 可选类型过滤：episodic/semantic/entity/reflection/permanent
    """
    try:
        deps = _deps()
        if deps is None:
            return _store_not_ready()
        mt = None
        if memory_type:
            try:
                mt = MemoryType(memory_type)
            except ValueError:
                log("memory_deep_search 异常已忽略", "DEBUG")
        result = await deps.store.list_paginated(page=page, page_size=page_size, memory_type=mt)
        return json.dumps(result, ensure_ascii=False)
    except Exception as e:
        return error_from_exception(e, action="深度搜索记忆")


@deferred_tool(
    group="memory", tags=["core", "heartbeat"], source="mind.memory",
    description="将多条记忆合并为一条。旧记忆不会删除但会被标记为低优先级。用于整理和压缩过多的同类记忆。",
)
async def merge_memories(memory_ids: str, merged_content: str) -> str:
    """将多条记忆合并为一条（有效分最高者原地存活，其余并入并保留重定向）。

    Args:
        memory_ids: 要合并的记忆 ID 列表，逗号分隔（如 1,5,12）
        merged_content: 合并后的内容（综合多条记忆的精华）
    """
    try:
        deps = _deps()
        if deps is None:
            return _store_not_ready()

        ids = []
        for s in memory_ids.split(","):
            s = s.strip()
            if s.isdigit():
                ids.append(int(s))
        if len(ids) < 2:
            return tool_error("至少需要 2 条记忆才能合并", cause=ErrorCause.PARAM, retryable=False)
        if not merged_content.strip():
            return tool_error("合并内容不能为空", cause=ErrorCause.PARAM, retryable=False)

        keep_id = await deps.store.merge_memories(ids, merged_content, actor="tool:merge")
        if not keep_id:
            return tool_error(
                "合并失败：可合并的记忆不足（不存在，或为永久记忆/画像/规划等系统独占条目）",
                cause=ErrorCause.NOT_FOUND, retryable=False,
                hint="先用 get_memory 确认要合并的记忆状态；系统独占条目不参与合并",
            )

        wake_embedding_worker()

        return json.dumps({
            "ok": True, "keep_id": keep_id,
            "merged_from": [i for i in ids if i != keep_id],
            "message": f"已合并为一条（存活 id={keep_id}，其余 id 已重定向到本条）",
        }, ensure_ascii=False)
    except Exception as e:
        return error_from_exception(e, action="合并记忆")


# ------------------------------------------------------------------
# 心跳日志
# ------------------------------------------------------------------

@deferred_tool(
    group="memory", tags=["heartbeat"], source="mind.heartbeat",
    description=(
        "将内容写入心跳工作日志。用于在执行任务后记录操作总结。"
        "在 end_reply 之前调用，简要记录本次做了什么。"
    ),
)
async def log_to_heartbeat(content: str) -> str:
    """将一条记录写入心跳工作日志。

    Args:
        content: 日志内容（简要总结，一两句话）
    """
    try:
        from agent.heartbeat.log import append_entry
        append_entry(content)
        return json.dumps({"ok": True, "message": "已写入心跳日志"}, ensure_ascii=False)
    except Exception as e:
        return error_from_exception(e, action="写入心跳日志")


@deferred_tool(
    group="memory", tags=["heartbeat"], source="mind.heartbeat",
    description=(
        "读取最近的心跳工作日志（自己在心跳/任务中经 log_to_heartbeat 记录的操作总结，"
        "以及系统维护摘要）。回顾近期自主工作、核实某任务是否真的做过时使用。"
    ),
)
async def get_heartbeat_log(count: int = 3) -> str:
    """读取最近 N 条心跳日志块（新→旧原文）。

    Args:
        count: 读取的日志块数（1-20，默认 3；每块对应一次心跳周期）
    """
    try:
        from agent.heartbeat.log import load_recent
        entries = max(1, min(20, int(count)))
        text = load_recent(entries)
        if not text.strip():
            return json.dumps({
                "ok": True, "count": 0, "log": "",
                "message": "暂无心跳日志（尚无心跳周期或日志已被裁剪）",
            }, ensure_ascii=False)
        return json.dumps({
            "ok": True, "count": entries,
            "log": text[-8000:],  # 超长截尾保最近条目
        }, ensure_ascii=False)
    except Exception as e:
        return error_from_exception(e, action="读取心跳日志")


# ------------------------------------------------------------------
# 任务执行
# ------------------------------------------------------------------

@deferred_tool(
    group="memory", tags=["core", "heartbeat"], source="mind.memory",
    description=(
        "列出所有可执行的任务（含每个任务最近一次执行概况：时间/时长/状态/触发来源）。"
        "任务是预定义的流程化工作，可按名称触发执行；需要历次执行明细用 task_history。"
    ),
)
async def list_tasks() -> str:
    """列出所有可执行的任务。"""
    try:
        from agent.runtime.singleton import require_runtime
        engine = require_runtime().mind.heartbeat_engine
        tasks = engine.task_registry.list_info()
        from agent.task.history import get_summary
        summary = get_summary()
        for item in tasks:
            last = summary.get(str(item.get("name") or ""))
            if last is not None:
                item["last_run"] = _with_readable_time(last)
        return json.dumps({"total": len(tasks), "tasks": tasks}, ensure_ascii=False)
    except Exception as e:
        return error_from_exception(e, action="列出任务")


def _with_readable_time(record: Dict[str, Any]) -> Dict[str, Any]:
    """执行概况副本附可读时间文本（epoch 保留，模型直接读文本）。"""
    import time as _time
    readable = dict(record)
    started_at = float(record.get("started_at") or 0.0)
    if started_at > 0:
        readable["started_at_text"] = _time.strftime("%Y-%m-%d %H:%M", _time.localtime(started_at))
    return readable


@deferred_tool(
    group="memory", tags=["core", "heartbeat"], source="mind.task",
    description=(
        "查看指定任务最近几次的执行记录（每次的开始时间/耗时/状态/触发来源/"
        "产出摘要/错误信息）。评估任务是否按时运行、是否反复失败、上次产出了什么时使用。"
    ),
)
async def task_history(task_name: str) -> str:
    """查看任务执行历史（新→旧）。

    Args:
        task_name: 任务名称（通过 list_tasks 获取）
    """
    try:
        from agent.task.history import get_history
        records = get_history(task_name)
        if not records:
            return json.dumps({
                "ok": True, "task": task_name, "count": 0, "records": [],
                "message": "该任务暂无执行记录（可能从未执行过，或记录已被任务删除清理）",
            }, ensure_ascii=False)
        rendered = []
        for rec in records:
            from agent.task.history import format_duration_ms
            item = _with_readable_time(rec)
            item["duration_text"] = format_duration_ms(int(rec.get("duration_ms") or 0))
            rendered.append(item)
        return json.dumps({
            "ok": True, "task": task_name, "count": len(rendered),
            "records": rendered,
        }, ensure_ascii=False)
    except Exception as e:
        return error_from_exception(e, action="查询任务执行历史")


@deferred_tool(
    group="memory", tags=["core", "heartbeat"], source="mind.memory",
    description=(
        "按名称执行指定任务。任务在后台异步执行，可通过 list_tasks 查看可用任务。"
        "任务执行期间会调用工具完成具体工作（如整理画像、清理记忆等）。"
    ),
)
async def execute_task(task_name: str) -> str:
    """按名称执行指定任务（后台异步，立即返回受理结果）。

    Args:
        task_name: 任务名称（通过 list_tasks 获取）
    """
    try:
        from agent.runtime.singleton import require_runtime
        engine = require_runtime().mind.heartbeat_engine
        ok, message = engine.start_task_background(task_name)
        return json.dumps({"ok": ok, "task": task_name, "message": message}, ensure_ascii=False)
    except Exception as e:
        return error_from_exception(e, action="执行任务")


# ------------------------------------------------------------------
# 实体画像管理
# ------------------------------------------------------------------

@deferred_tool(
    group="memory", tags=["core", "heartbeat"], source="mind.memory",
    description=(
        "列出所有已知的实体画像（用户/群组）。"
        "返回每个实体的 scope、对话次数、画像摘要和跨平台关联信息。"
        "linked_to 非空表示该实体是别名，画像存储在 linked_to 指向的主身份上。"
    ),
)
async def list_entity_profiles() -> str:
    """列出所有已知的实体画像摘要（含跨平台关联信息）。"""
    try:
        sqlite = _get_sqlite()
        profiles = await sqlite.list_entity_profiles()
        all_aliases = await sqlite.list_aliases()

        alias_map: dict[str, str] = {}
        primary_aliases: dict[str, list[str]] = {}
        for a in all_aliases:
            src = f"{a['scope_type']}:{a['scope_id']}"
            dst = f"{a['primary_scope_type']}:{a['primary_scope_id']}"
            alias_map[src] = dst
            primary_aliases.setdefault(dst, []).append(src)

        items = []
        for p in profiles:
            personality = p.get("personality") or ""
            scope = f"{p['scope_type']}:{p['scope_id']}"
            item: dict = {
                "scope": scope,
                "conv_num": p.get("conv_num", 0),
                "conv_update_num": p.get("conv_update_num", 0),
                "preview": personality[:120] + ("..." if len(personality) > 120 else ""),
            }
            if scope in alias_map:
                item["linked_to"] = alias_map[scope]
            if scope in primary_aliases:
                item["aliases"] = primary_aliases[scope]
            items.append(item)
        return json.dumps({"total": len(items), "profiles": items}, ensure_ascii=False)
    except Exception as e:
        return error_from_exception(e, action="列出实体画像")


@deferred_tool(
    group="memory", tags=["core", "heartbeat"], source="mind.memory",
    description=(
        "查看指定实体的完整画像内容（自动解析跨平台关联）。"
        "先用 list_entity_profiles 获取可用的 scope_type/scope_id。"
    ),
)
async def get_entity_profile(scope_type: str, scope_id: str) -> str:
    """查看指定实体的完整画像内容。

    Args:
        scope_type: 范围类型（user 或 group）
        scope_id: 用户 ID 或群组 ID（格式 ``{频道}:{id}``，如 qq:123；传裸 id 时按当前会话频道解析）
    """
    try:
        sqlite = _get_sqlite()
        scope_id = _normalize_scope_id(scope_id)
        primary = await sqlite.resolve_alias(scope_type, scope_id)
        p_type, p_id = primary if primary else (scope_type, scope_id)

        data = await sqlite.get_entity_personality(scope_type=p_type, scope_id=p_id)
        if not data:
            return tool_error(f"{scope_type}:{scope_id} 暂无画像", cause=ErrorCause.NOT_FOUND, retryable=False)

        result: dict = {
            "scope": f"{p_type}:{p_id}",
            "personality": data.get("personality", ""),
            "conv_num": data.get("conv_num", 0),
            "conv_update_num": data.get("conv_update_num", 0),
        }
        if primary:
            result["queried_as"] = f"{scope_type}:{scope_id}"
        aliases = await sqlite.get_aliases_for_primary(p_type, p_id)
        if aliases:
            result["aliases"] = [f"{a['scope_type']}:{a['scope_id']}" for a in aliases]
        return json.dumps(result, ensure_ascii=False)
    except Exception as e:
        return error_from_exception(e, action="读取实体画像")


@deferred_tool(
    group="memory", tags=["core", "heartbeat"], source="mind.memory",
    description=(
        "删除指定实体的画像。用于清理无意义或不再需要的实体画像（如临时用户、测试数据等）。"
        "删除前建议先用 get_entity_profile 确认内容。"
    ),
)
async def delete_entity_profile(scope_type: str, scope_id: str) -> str:
    """删除指定实体的画像。

    Args:
        scope_type: 范围类型（user 或 group）
        scope_id: 用户 ID 或群组 ID（格式 ``{频道}:{id}``，如 qq:123；传裸 id 时按当前会话频道解析）
    """
    try:
        sqlite = _get_sqlite()
        scope_id = _normalize_scope_id(scope_id)
        # 与 update 路径一致：先解析别名到主身份，避免别名场景残留旧画像/记忆
        primary = await sqlite.resolve_alias(scope_type, scope_id)
        p_type, p_id = primary if primary else (scope_type, scope_id)

        existing = await sqlite.get_entity_personality(scope_type=p_type, scope_id=p_id)
        if not existing:
            return tool_error(f"{scope_type}:{scope_id} 不存在", cause=ErrorCause.NOT_FOUND, retryable=False)

        await sqlite.delete_entity_profile(scope_type=p_type, scope_id=p_id)

        # 清理内存缓存（primary + 所有 alias 的内存实体）
        try:
            from agent.runtime.singleton import require_runtime
            rt = require_runtime()
            entities = rt.data_center.everything_data.entities
            entities.pop(f"{p_type}_{p_id}", None)
            aliases = await sqlite.get_aliases_for_primary(p_type, p_id)
            for a in aliases:
                entities.pop(f"{a['scope_type']}_{a['scope_id']}", None)
        except Exception:
            log("delete_entity_profile 异常已忽略", "DEBUG")

        # 清理 MemoryStore 中的 ENTITY 记忆
        deps = _deps()
        if deps is not None:
            source = f"entity_{p_id}"
            old_entries = await deps.store.list_recent(
                limit=5, memory_type=MemoryType.ENTITY, source=source,
            )
            for entry in old_entries:
                if entry.id:
                    await deps.store.delete(entry.id, actor="tool:entity_profile")

        return json.dumps({
            "ok": True,
            "message": f"已删除 {scope_type}:{scope_id} 的画像",
        }, ensure_ascii=False)
    except Exception as e:
        return error_from_exception(e, action="删除实体画像")


@deferred_tool(
    group="memory", tags=["core", "heartbeat"], source="mind.memory",
    description=(
        "更新指定实体的画像内容（自动解析跨平台关联，写入主身份）。"
        "建议先用 get_entity_profile 查看当前画像，在此基础上增量更新。"
        "更新后 conv_update_num 归零，重新计算下次自动分析的触发。"
    ),
)
async def update_entity_profile(scope_type: str, scope_id: str, personality: str) -> str:
    """更新指定实体的画像内容。

    Args:
        scope_type: 范围类型（user 或 group）
        scope_id: 用户 ID 或群组 ID（格式 ``{频道}:{id}``，如 qq:123；传裸 id 时按当前会话频道解析）
        personality: 新的画像内容（Markdown 格式的结构化描述）
    """
    try:
        if not personality.strip():
            return tool_error("画像内容不能为空", cause=ErrorCause.PARAM, retryable=False)

        sqlite = _get_sqlite()
        scope_id = _normalize_scope_id(scope_id)
        primary = await sqlite.resolve_alias(scope_type, scope_id)
        p_type, p_id = primary if primary else (scope_type, scope_id)

        old = await sqlite.get_entity_personality(scope_type=p_type, scope_id=p_id)
        conv_num = old.get("conv_num", 0) if old else 0

        # 覆盖式更新前备份旧画像（防坏写不可恢复）
        if old and old.get("personality"):
            from .profile_backup import backup_entity_profile
            backup_entity_profile(p_type, p_id, old["personality"])

        await sqlite.set_entity_personality(
            scope_type=p_type, scope_id=p_id, personality=personality.strip(),
            conv_num=conv_num, conv_update_num=0,
        )

        # 同步更新内存缓存（primary + 所有 alias 的内存实体）
        try:
            from agent.runtime.singleton import require_runtime
            rt = require_runtime()
            entities = rt.data_center.everything_data.entities
            keys_to_update = [f"{p_type}_{p_id}"]
            aliases = await sqlite.get_aliases_for_primary(p_type, p_id)
            keys_to_update.extend(f"{a['scope_type']}_{a['scope_id']}" for a in aliases)
            for key in keys_to_update:
                entity = entities.get(key)
                if entity:
                    entity.set_personality(personality.strip())
        except Exception:
            log("update_entity_profile 异常已忽略", "DEBUG")

        # 同步更新 MemoryStore 中的 ENTITY 记忆
        deps = _deps()
        if deps is not None:
            from .self_profile import PROFILE_MEMORY_IMPORTANCE
            source = f"entity_{p_id}"
            scope_tag = f"{p_type}:{p_id}"
            old_entries = await deps.store.list_recent(
                limit=5, memory_type=MemoryType.ENTITY, source=source,
            )
            for old_entry in old_entries:
                if old_entry.id:
                    await deps.store.delete(old_entry.id, actor="tool:entity_profile")
            entry = MemoryEntry(
                memory_type=MemoryType.ENTITY,
                content=personality.strip(),
                source=source,
                tags=[scope_tag],
                importance=PROFILE_MEMORY_IMPORTANCE,
            )
            await deps.store.add(entry, actor="tool:entity_profile")
            wake_embedding_worker()

        target_desc = f"{p_type}:{p_id}"
        if primary:
            target_desc += f" (通过 {scope_type}:{scope_id})"
        return json.dumps({
            "ok": True,
            "message": f"已更新 {target_desc} 的画像",
            "preview": personality.strip()[:100],
        }, ensure_ascii=False)
    except Exception as e:
        return error_from_exception(e, action="更新实体画像")


# ------------------------------------------------------------------
# 跨平台实体关联
# ------------------------------------------------------------------

@deferred_tool(
    group="memory", tags=["core", "heartbeat"], source="mind.memory",
    description=(
        "将两个不同平台的实体关联为同一个人/群组。"
        "source 将成为 target 的别名，画像共享 target 的内容。"
        "关联后两个身份的对话记录保持独立，但画像分析会合并所有对话。"
        "建议关联后用 update_entity_profile 合并两方的画像内容。"
    ),
)
async def link_entity(
    source_scope_type: str,
    source_scope_id: str,
    target_scope_type: str,
    target_scope_id: str,
) -> str:
    """将 source 实体关联到 target（target 成为主身份）。

    Args:
        source_scope_type: 源实体类型（user 或 group）
        source_scope_id: 源实体 ID（格式 ``{频道}:{id}``，如 qq:123；传裸 id 时按当前会话频道解析）
        target_scope_type: 目标实体类型（user 或 group）
        target_scope_id: 目标实体 ID（格式 ``{频道}:{id}``，如 qq:123；传裸 id 时按当前会话频道解析）
    """
    try:
        if source_scope_type == target_scope_type and source_scope_id == target_scope_id:
            return tool_error("不能将实体关联到自身", cause=ErrorCause.PARAM, retryable=False)

        sqlite = _get_sqlite()
        source_scope_id = _normalize_scope_id(source_scope_id)
        target_scope_id = _normalize_scope_id(target_scope_id)

        # 追踪 target 的最终 primary（避免链式别名）
        target_primary = await sqlite.resolve_alias(target_scope_type, target_scope_id)
        final_type, final_id = target_primary if target_primary else (target_scope_type, target_scope_id)

        # 检查 source 是否已有不同的 primary
        existing = await sqlite.resolve_alias(source_scope_type, source_scope_id)
        if existing and (existing[0] != final_type or existing[1] != final_id):
            return tool_error(
                f"{source_scope_type}:{source_scope_id} 已关联到 "
                f"{existing[0]}:{existing[1]}，需先 unlink_entity 解除",
                cause=ErrorCause.STATE, retryable=False,
            )

        await sqlite.set_alias(
            scope_type=source_scope_type, scope_id=source_scope_id,
            primary_scope_type=final_type, primary_scope_id=final_id,
        )
        from .graph.tools import invalidate_alias_cache
        invalidate_alias_cache()

        return json.dumps({
            "ok": True,
            "message": (
                f"已将 {source_scope_type}:{source_scope_id} "
                f"关联到 {final_type}:{final_id}"
            ),
            "source": f"{source_scope_type}:{source_scope_id}",
            "primary": f"{final_type}:{final_id}",
        }, ensure_ascii=False)
    except Exception as e:
        return error_from_exception(e, action="关联实体")


@deferred_tool(
    group="memory", tags=["core", "heartbeat"], source="mind.memory",
    description=(
        "解除一个实体的跨平台关联，使其恢复为独立实体。"
        "解除后该实体将拥有独立的画像（可选择复制当前主身份的画像）。"
    ),
)
async def unlink_entity(scope_type: str, scope_id: str, copy_profile: bool = True) -> str:
    """解除实体的跨平台关联。

    Args:
        scope_type: 实体类型（user 或 group）
        scope_id: 实体 ID（格式 ``{频道}:{id}``，如 qq:123；传裸 id 时按当前会话频道解析）
        copy_profile: 是否将当前主身份的画像复制一份给自己（默认 True）
    """
    try:
        sqlite = _get_sqlite()
        scope_id = _normalize_scope_id(scope_id)
        primary = await sqlite.resolve_alias(scope_type, scope_id)
        if not primary:
            return tool_error(
                f"{scope_type}:{scope_id} 没有关联关系",
                cause=ErrorCause.NOT_FOUND, retryable=False,
            )

        # 复制 primary 的画像到自己名下
        if copy_profile:
            primary_data = await sqlite.get_entity_personality(
                scope_type=primary[0], scope_id=primary[1],
            )
            if primary_data and primary_data.get("personality"):
                await sqlite.set_entity_personality(
                    scope_type=scope_type, scope_id=scope_id,
                    personality=primary_data["personality"],
                )

        removed = await sqlite.remove_alias(scope_type=scope_type, scope_id=scope_id)
        if not removed:
            return tool_error("解除关联失败", cause=ErrorCause.INTERNAL)
        from .graph.tools import invalidate_alias_cache
        invalidate_alias_cache()

        return json.dumps({
            "ok": True,
            "message": (
                f"已解除 {scope_type}:{scope_id} 与 "
                f"{primary[0]}:{primary[1]} 的关联"
            ),
            "copied_profile": copy_profile,
        }, ensure_ascii=False)
    except Exception as e:
        return error_from_exception(e, action="解除实体关联")


# ------------------------------------------------------------------
# 工具错误查询
# ------------------------------------------------------------------

@deferred_tool(
    group="memory", tags=["core", "heartbeat"], source="mind.memory",
    description="查询工具调用错误历史，用于反思和总结经验。空 tool_name 返回所有工具的错误统计。",
)
async def recall_tool_errors(tool_name: str = "", limit: int = 20) -> str:
    """查询工具调用错误历史。

    Args:
        tool_name: 工具名称，空则返回所有工具的错误统计摘要
        limit: 返回条数上限，默认 20
    """
    deps = _deps()
    if deps is None:
        return _store_not_ready()
    try:
        limit = int(limit)
        if not tool_name:
            stats = await deps.store.get_tool_error_stats()
            return json.dumps({"stats": stats}, ensure_ascii=False)
        errors = await deps.store.get_tool_errors(tool_name=tool_name, limit=limit)
        return json.dumps({
            "tool_name": tool_name,
            "count": len(errors),
            "errors": errors,
        }, ensure_ascii=False)
    except Exception as e:
        return error_from_exception(e, action="查询工具错误历史")
