"""内部记忆工具 — Agent 大脑的长期记忆接口。

记忆是 Agent 的核心认知能力，工具直接持有 MemoryStore / Embedder 引用，
通过 `register_memory_tools()` 在运行时注入依赖后批量注册到 EntityRegistry。
"""

from __future__ import annotations

import json
from typing import Optional

from entities._sdk import deferred_tool, activate_group
from core.log import log

from .memory_store import MemoryStore
from .memory_types import MemoryEntry, MemoryType
from .embedder import Embedder

_store: Optional[MemoryStore] = None
_embedder: Optional[Embedder] = None

_TYPE_MAP = {
    "trait": MemoryType.ENTITY,
    "event": MemoryType.EPISODIC,
    "fact": MemoryType.SEMANTIC,
    "reflection": MemoryType.REFLECTION,
    "entity": MemoryType.ENTITY,
    "permanent": MemoryType.PERMANENT,
}


def register_memory_tools(store: MemoryStore, embedder: Embedder) -> None:
    """注入运行时依赖并批量注册记忆工具。"""
    global _store, _embedder
    _store = store
    _embedder = embedder
    count = activate_group("memory", "长期记忆 - 记忆存储、语义检索、标签索引、遗忘")
    log(f"💾 内部记忆工具已注册 ({count} 个)", tag="思维")


# ------------------------------------------------------------------
# 工具实现
# ------------------------------------------------------------------

@deferred_tool(
    group="memory", tags=["always"], source="mind.memory",
    description="将一条关键信息存入长期记忆。内容应简洁精炼。使用 type:permanent 标签可存储永远不会被遗忘的重要信息。",
)
async def memorize(content: str, tags: str = "", importance: float = 0.7) -> str:
    """将一条关键信息存入长期记忆。

    Args:
        content: 要记住的内容（简洁扼要，一两句话）
        tags: 标签，逗号分隔。推荐前缀：type:(fact/event/permanent) user:(uid) group:(id) topic:(主题) channel:(频道)。type:permanent 表示永久记忆
        importance: 重要性 0-1，默认 0.7。permanent 类型自动设为 1.0
    """
    try:
        if not _store:
            return json.dumps({"error": "记忆系统未初始化"}, ensure_ascii=False)

        tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else []

        mem_type = MemoryType.SEMANTIC
        for t in tag_list:
            if t.startswith("type:"):
                mem_type = _TYPE_MAP.get(t.split(":", 1)[1], MemoryType.SEMANTIC)
                break

        if mem_type == MemoryType.PERMANENT:
            importance = 1.0
            return await _upsert_permanent(content, tag_list, importance)

        if await _store.has_similar_content(content):
            return json.dumps({"ok": False, "message": "已存在相似记忆，跳过"}, ensure_ascii=False)

        entry = MemoryEntry(
            memory_type=mem_type,
            content=content,
            tags=tag_list,
            importance=max(0.0, min(1.0, importance)),
        )

        if _embedder and _embedder.available:
            vec = await _embedder.embed_one(content)
            if vec:
                entry.embedding = vec

        mid = await _store.add(entry)
        return json.dumps({"ok": True, "id": mid, "tags": tag_list}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


async def _upsert_permanent(content: str, tag_list: list[str], importance: float) -> str:
    """永久记忆的 upsert：按非 type: 标签匹配已有条目，存在则更新，不存在则新增。"""
    assert _store is not None
    match_tags = [t for t in tag_list if not t.startswith("type:")]
    existing: list[MemoryEntry] = []
    if match_tags:
        candidates = await _store.search_by_tags(match_tags, limit=10)
        existing = [e for e in candidates if e.memory_type == MemoryType.PERMANENT]

    new_embedding = None
    if _embedder and _embedder.available:
        new_embedding = await _embedder.embed_one(content)

    if existing:
        target = existing[0]
        old_preview = target.content[:60]
        target.content = content
        target.tags = tag_list
        target.importance = importance
        if new_embedding:
            target.embedding = new_embedding
        await _store.update(target)
        return json.dumps({
            "ok": True, "id": target.id, "action": "updated",
            "old_preview": old_preview, "tags": tag_list,
        }, ensure_ascii=False)

    entry = MemoryEntry(
        memory_type=MemoryType.PERMANENT,
        content=content,
        tags=tag_list,
        importance=importance,
        embedding=new_embedding,
    )
    mid = await _store.add(entry)
    return json.dumps({
        "ok": True, "id": mid, "action": "created", "tags": tag_list,
    }, ensure_ascii=False)


@deferred_tool(
    group="memory", tags=["always"], source="mind.memory",
    description="在长期记忆中语义搜索，返回最相关的记忆。",
)
async def recall(query: str, tags: str = "", limit: int = 5) -> str:
    """在长期记忆中语义搜索（同时检索 memories 表和 MD 文件索引）。

    Args:
        query: 搜索查询（自然语言）
        tags: 可选标签过滤，逗号分隔（如 user:123）
        limit: 最大返回数量，默认 5
    """
    try:
        if not _store:
            return json.dumps({"error": "记忆系统未初始化"}, ensure_ascii=False)

        tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else None

        query_vec = None
        if _embedder and _embedder.available:
            query_vec = await _embedder.embed_one(query)

        from .cognee.config import load_cognee_config
        from .cognee.fusion import federated_search
        from .cognee.runtime import get_cognee_client
        cognee_config = load_cognee_config()
        entity_scope = ""
        for tag in tag_list or []:
            if tag.startswith(("user:", "group:")):
                scope_type, scope_id = tag.split(":", 1)
                entity_scope = f"{scope_type}_{scope_id}"
                break
        results = await federated_search(
            _store.search_unified(
                query=query,
                query_vec=query_vec,
                query_tags=tag_list,
                limit=limit * cognee_config.recall_pool_multiplier,
            ),
            query=query,
            client=get_cognee_client(),
            config=cognee_config,
            limit=limit,
            entity_scope=entity_scope,
            query_tags=tag_list,
        )

        mem_ids = [
            int(r.id.split(":")[1])
            for r in results
            if r.source == "memory" and r.id.startswith("mem:")
        ]
        if mem_ids:
            await _store.record_access(mem_ids)

        items = [{
            "id": r.id,
            "content": r.snippet[:300],
            "tags": r.tags,
            "type": r.memory_type or "",
            "source": r.source,
            "path": r.path,
            "score": round(r.score, 3),
            "dataset": r.dataset_name,
            "provenance": r.provenance,
        } for r in results]
        return json.dumps({"count": len(items), "results": items}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


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
        if not _store:
            return json.dumps({"error": "记忆系统未初始化"}, ensure_ascii=False)

        if not tag:
            tag_counts = await _store.list_tags()
            total = await _store.count()
            index_status = await _store.get_index_status()
            return json.dumps({
                "total_memories": total,
                "tags": tag_counts,
                "index": index_status,
            }, ensure_ascii=False)

        entries = await _store.search_by_tags([tag], limit=20)
        items = [{
            "id": e.id,
            "summary": e.content[:80],
            "tags": e.tags,
            "importance": round(e.importance, 2),
        } for e in entries]
        return json.dumps({"tag": tag, "count": len(items), "memories": items}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


@deferred_tool(
    group="memory", tags=["core", "heartbeat"], source="mind.memory",
    description="按 ID 获取一条记忆的完整内容、标签、类型和重要性。用于在修改前先确认记忆的当前状态。",
)
async def get_memory(memory_id: int) -> str:
    """按 ID 获取一条记忆的完整信息。

    Args:
        memory_id: 记忆 ID（从 recall / memory_index / memory_deep_search 结果中获取）
    """
    try:
        if not _store:
            return json.dumps({"error": "记忆系统未初始化"}, ensure_ascii=False)
        entry = await _store.get(memory_id)
        if not entry:
            return json.dumps({"error": f"记忆 {memory_id} 不存在"}, ensure_ascii=False)
        return json.dumps({
            "id": entry.id,
            "type": entry.memory_type.value,
            "content": entry.content,
            "tags": entry.tags,
            "importance": round(entry.importance, 3),
            "source": entry.source,
            "access_count": entry.access_count,
        }, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


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
) -> str:
    """原地更新一条记忆（保留 id 和创建时间戳）。

    Args:
        memory_id: 要更新的记忆 ID
        content: 新的记忆内容（留空则保持原内容不变）
        tags: 新的标签，逗号分隔（留空则保持原标签不变）
        importance: 新的重要性 0-1（传 -1 则保持原值不变）
    """
    try:
        if not _store:
            return json.dumps({"error": "记忆系统未初始化"}, ensure_ascii=False)

        entry = await _store.get(memory_id)
        if not entry:
            return json.dumps({"error": f"记忆 {memory_id} 不存在"}, ensure_ascii=False)

        changed: list[str] = []
        content_changed = False

        if content.strip() and content.strip() != entry.content:
            entry.content = content.strip()
            changed.append("content")
            content_changed = True

        if tags.strip():
            new_tags = [t.strip() for t in tags.split(",") if t.strip()]
            if new_tags != entry.tags:
                entry.tags = new_tags
                changed.append("tags")

        if 0.0 <= importance <= 1.0 and round(importance, 3) != round(entry.importance, 3):
            entry.importance = importance
            changed.append("importance")

        if not changed:
            return json.dumps({"ok": True, "message": "无变更"}, ensure_ascii=False)

        # 内容变更时重新生成 embedding
        if content_changed and _embedder and _embedder.available:
            vec = await _embedder.embed_one(entry.content)
            if vec:
                entry.embedding = vec

        ok = await _store.update(entry)
        return json.dumps({
            "ok": ok,
            "id": memory_id,
            "changed": changed,
            "content_preview": entry.content[:100],
            "tags": entry.tags,
            "importance": round(entry.importance, 3),
        }, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


@deferred_tool(group="memory", tags=["core", "heartbeat"], source="mind.memory")
async def forget(memory_id: int) -> str:
    """删除指定 ID 的记忆。

    Args:
        memory_id: 要删除的记忆 ID
    """
    try:
        if not _store:
            return json.dumps({"error": "记忆系统未初始化"}, ensure_ascii=False)
        ok = await _store.delete(memory_id)
        return json.dumps({"ok": ok, "message": f"记忆 {memory_id} {'已遗忘' if ok else '不存在'}"}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


# ------------------------------------------------------------------
# 跨频道会话管理
# ------------------------------------------------------------------

def _get_sqlite():
    from services._runtime import require_runtime
    return require_runtime().data_center.sqlite


def _get_channel_manager():
    from agent.channel import get_channel_manager
    return get_channel_manager()


@deferred_tool(
    group="memory", tags=["core", "heartbeat", "always"], source="mind.memory",
    description=(
        "列出所有会话记录（用户/群组），了解有哪些对话历史可查阅。"
        "返回各 scope 的消息数量，便于选择要操作的会话。"
        "跨频道协同时，先用此工具了解有哪些会话。"
    ),
)
async def list_conversations() -> str:
    """列出所有会话记录的 scope（用户/群组），了解有哪些对话历史。

    返回 scope_type（user/group）、scope_id（用户或群组 ID）和消息数量。
    可用于跨频道查看不同用户/群组的对话情况。
    """
    try:
        scopes = await _get_sqlite().list_conversation_scopes()
        return json.dumps({"scopes": scopes, "total": len(scopes)}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


@deferred_tool(
    group="memory", tags=["core", "heartbeat", "always"], source="mind.memory",
    description=(
        "获取指定用户或群组的最近对话记录。"
        "可查阅任意频道的会话历史，实现跨频道信息协同。"
        "先用 list_conversations 了解有哪些 scope 可查。"
    ),
)
async def get_conversation(scope_type: str, scope_id: str, limit: int = 30) -> str:
    """获取指定用户或群组的最近对话记录。

    Args:
        scope_type: 范围类型（user 或 group）
        scope_id: 用户 ID 或群组 ID
        limit: 最大返回条数，默认 30，最大 100
    """
    try:
        sqlite = _get_sqlite()
        limit = max(1, min(limit, 100))
        records = await sqlite.fetch_conversation_with_id(
            scope_type=scope_type, scope_id=scope_id, limit=limit,
        )
        import datetime
        items = []
        for r in records:
            ts_sec = r["ts_ns"] // 1_000_000_000
            dt = datetime.datetime.fromtimestamp(ts_sec).strftime("%Y-%m-%d %H:%M:%S")
            items.append({
                "id": r["id"],
                "role": r["role"],
                "content": r["content"],
                "time": dt,
            })
        return json.dumps({
            "scope": f"{scope_type}:{scope_id}",
            "count": len(items),
            "messages": items,
        }, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


@deferred_tool(
    group="memory", tags=["core", "heartbeat"], source="mind.memory",
    description=(
        "向指定用户或群组的会话追加一条消息记录。"
        "可用于跨频道协同时在另一个会话中留言或记录信息。"
        "注意：此工具只写入对话历史，不会实际发送消息到频道。"
    ),
)
async def add_conversation_message(
    scope_type: str,
    scope_id: str,
    role: str,
    content: str,
) -> str:
    """向指定用户或群组的会话追加一条消息记录。

    Args:
        scope_type: 范围类型（user 或 group）
        scope_id: 用户 ID 或群组 ID
        role: 消息角色（user 或 assistant）
        content: 消息内容
    """
    try:
        if role not in ("user", "assistant"):
            return json.dumps({"error": "role 必须是 user 或 assistant"}, ensure_ascii=False)
        if not content.strip():
            return json.dumps({"error": "content 不能为空"}, ensure_ascii=False)
        sqlite = _get_sqlite()
        await sqlite.append_conversation(
            scope_type=scope_type, scope_id=scope_id,
            role=role, content=content,
        )
        return json.dumps({
            "ok": True,
            "message": f"已向 {scope_type}:{scope_id} 追加消息",
            "role": role,
            "content_preview": content[:100],
        }, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


@deferred_tool(
    group="memory", tags=["core", "heartbeat"], source="mind.memory",
    description=(
        "修改指定会话中的一条消息内容。"
        "需要先通过 get_conversation 获取消息的 row_id。"
    ),
)
async def update_conversation_message(row_id: int, new_content: str) -> str:
    """修改指定会话中的一条消息内容。

    Args:
        row_id: 消息的行 ID（通过 get_conversation 获取）
        new_content: 新的消息内容
    """
    try:
        if not new_content.strip():
            return json.dumps({"error": "new_content 不能为空"}, ensure_ascii=False)
        sqlite = _get_sqlite()
        db = await sqlite._get_db()
        cursor = await db.execute(
            "UPDATE conversation_messages SET content=? WHERE id=?",
            (new_content, row_id),
        )
        if cursor.rowcount == 0:
            return json.dumps({"error": f"消息 {row_id} 不存在"}, ensure_ascii=False)
        await db.commit()
        return json.dumps({
            "ok": True,
            "message": f"消息 {row_id} 已更新",
            "new_content_preview": new_content[:100],
        }, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


@deferred_tool(
    group="memory", tags=["core", "always"], source="mind.memory",
    description=(
        "列出所有已连接的通信频道及其状态。"
        "了解当前可用的频道、账号信息和连接状态。"
        "跨频道协同时，先用此工具了解有哪些频道可操作。"
    ),
)
async def list_active_channels() -> str:
    """列出所有已连接的通信频道及其状态。

    返回频道 ID、名称、类型、连接状态和账号信息。
    """
    try:
        cm = _get_channel_manager()
        channels = cm.list_channels()
        result = []
        for key, ch in channels.items():
            info = ch.get_status_info()
            result.append({
                "channel_id": key,
                "name": info.get("name", key),
                "type": info.get("type", "unknown"),
                "status": info.get("status", "unknown"),
                "bot_username": info.get("bot_username", ""),
                "self_id": info.get("self_id", ""),
                "capabilities": [c.value for c in ch.capabilities],
            })
        return json.dumps({
            "channels": result,
            "total": len(result),
        }, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


@deferred_tool(
    group="memory", tags=["core", "heartbeat"], source="mind.memory",
    description=(
        "删除一条会话记录。需要先通过 get_conversation 获取消息的 row_id。"
        "可用于清理错误消息或敏感内容。"
    ),
)
async def delete_conversation_message(row_id: int) -> str:
    """删除一条会话记录。

    Args:
        row_id: 消息的行 ID（通过 get_conversation 获取）
    """
    try:
        await _get_sqlite().delete_conversation_by_id(row_id)
        return json.dumps({"ok": True, "message": f"消息 {row_id} 已删除"}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


@deferred_tool(
    group="memory", tags=["core", "heartbeat"], source="mind.memory",
    description=(
        "清空指定用户或群组的全部会话记录。"
        "此操作不可恢复，请谨慎使用。"
        "可用于重置与某用户/群组的对话历史。"
    ),
)
async def clear_conversation(scope_type: str, scope_id: str) -> str:
    """清空指定用户或群组的全部会话记录。

    Args:
        scope_type: 范围类型（user 或 group）
        scope_id: 用户 ID 或群组 ID
    """
    try:
        count = await _get_sqlite().clear_conversation(scope_type=scope_type, scope_id=scope_id)
        return json.dumps({"ok": True, "cleared": count, "message": f"已清空 {scope_type}:{scope_id} 的 {count} 条记录"}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


@deferred_tool(
    group="memory", tags=["core", "heartbeat"], source="mind.memory",
    description=(
        "深度翻阅历史对话记录，语义搜索超出当前上下文窗口的旧聊天内容。"
        "当你隐约记得某件事但当前上下文中找不到时使用——就像翻阅自己的聊天记录。"
        "需要先通过 list_conversations 获取可用的 scope_type/scope_id。"
    ),
)
async def recall_conversation(
    query: str,
    scope_type: str,
    scope_id: str,
    limit: int = 5,
) -> str:
    """深度语义搜索历史对话（超出当前上下文窗口的部分）。

    Args:
        query: 要搜索的内容（自然语言描述，如"上次讨论的旅行计划"）
        scope_type: 对话类型（user 或 group）
        scope_id: 用户 ID 或群组 ID（可通过 list_conversations 查看）
        limit: 最大返回条数，默认 5，最大 10
    """
    import time as _time

    try:
        from agent.config import get_config_provider
        _cfg = get_config_provider().mind
        max_results: int = _cfg.conv_recall_max_results
        backfill_batch: int = _cfg.conv_recall_backfill_batch
        min_score: float = _cfg.conv_recall_min_score
        scan_limit: int = _cfg.conv_recall_scan_limit
        skip_recent: int = get_config_provider().config.max_conversation_size
    except Exception as e:
        from core.log import log as _log
        _log(f"对话检索配置加载失败，使用默认值: {e}", "DEBUG")
        max_results, backfill_batch, min_score, scan_limit, skip_recent = 10, 30, 0.25, 500, 30

    try:
        sqlite = _get_sqlite()
        limit = max(1, min(limit, max_results))

        # 先为该 scope 回填缺失的 embedding（批次由配置控制）
        if _embedder and _embedder.available:
            try:
                await sqlite.backfill_conversation_embeddings(
                    _embedder,
                    scope_type=scope_type,
                    scope_id=scope_id,
                    batch_size=backfill_batch,
                )
            except Exception as e:
                from core.log import log as _log
                _log(f"对话 embedding 回填失败: {e}", "DEBUG")

        t0 = _time.monotonic()
        results: list[dict] = []

        if _embedder and _embedder.available:
            query_vec = await _embedder.embed_one(query)
            if query_vec:
                results = await sqlite.search_conversation_vector(
                    scope_type, scope_id, query_vec,
                    limit=limit, skip_recent=skip_recent,
                    min_score=min_score, scan_limit=scan_limit,
                )

        # embedding 不可用或无结果时降级为关键词搜索
        if not results:
            keywords = [w.strip() for w in query.split() if len(w.strip()) >= 2][:5]
            results = await sqlite.search_conversation_keyword(
                scope_type, scope_id, keywords, limit=limit, skip_recent=skip_recent,
            )

        elapsed_ms = round((_time.monotonic() - t0) * 1000)

        if not results:
            return json.dumps({
                "count": 0,
                "results": [],
                "message": "未找到相关历史对话",
                "elapsed_ms": elapsed_ms,
            }, ensure_ascii=False)

        items = []
        for r in results:
            ts_sec = r["ts_ns"] // 1_000_000_000
            import datetime
            dt = datetime.datetime.fromtimestamp(ts_sec).strftime("%Y-%m-%d %H:%M")
            items.append({
                "role": r["role"],
                "content": r["content"],
                "time": dt,
                "score": round(r.get("score", 0.0), 3),
            })

        return json.dumps({
            "count": len(items),
            "results": items,
            "elapsed_ms": elapsed_ms,
        }, ensure_ascii=False)

    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


# ------------------------------------------------------------------
# 记忆统计、深度搜索、合并
# ------------------------------------------------------------------

@deferred_tool(
    group="memory", tags=["core", "heartbeat"], source="mind.memory",
    description="查看记忆系统统计和健康状态。返回各类型记忆数量、阈值预警、索引状态等信息。",
)
async def memory_stats() -> str:
    """查看记忆系统统计和健康状态。"""
    try:
        if not _store:
            return json.dumps({"error": "记忆系统未初始化"}, ensure_ascii=False)
        health = await _store.get_health_status()
        return json.dumps(health, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


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
        return json.dumps({"error": str(e)}, ensure_ascii=False)


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
            return json.dumps({"error": "Cognee 运行时未初始化"}, ensure_ascii=False)
        count = await coordinator.retry_failed()
        return json.dumps({"ok": True, "retried": count}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


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
            return json.dumps({"error": "Cognee 运行时未初始化"}, ensure_ascii=False)
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
        return json.dumps({"error": str(e)}, ensure_ascii=False)


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
            return json.dumps({"error": "Cognee 运行时未初始化"}, ensure_ascii=False)
        result = await coordinator.improve(dataset_name)
        value = result.model_dump(mode="json") if hasattr(result, "model_dump") else result
        return json.dumps({"ok": True, "result": value}, ensure_ascii=False, default=str)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


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
        if not _store:
            return json.dumps({"error": "记忆系统未初始化"}, ensure_ascii=False)
        mt = None
        if memory_type:
            try:
                mt = MemoryType(memory_type)
            except ValueError:
                pass
        result = await _store.list_paginated(page=page, page_size=page_size, memory_type=mt)
        return json.dumps(result, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


@deferred_tool(
    group="memory", tags=["core", "heartbeat"], source="mind.memory",
    description="将多条记忆合并为一条。旧记忆不会删除但会被标记为低优先级。用于整理和压缩过多的同类记忆。",
)
async def merge_memories(memory_ids: str, merged_content: str) -> str:
    """将多条记忆合并为一条新记忆。

    Args:
        memory_ids: 要合并的记忆 ID 列表，逗号分隔（如 1,5,12）
        merged_content: 合并后的内容（综合多条记忆的精华）
    """
    try:
        if not _store:
            return json.dumps({"error": "记忆系统未初始化"}, ensure_ascii=False)

        ids = []
        for s in memory_ids.split(","):
            s = s.strip()
            if s.isdigit():
                ids.append(int(s))
        if len(ids) < 2:
            return json.dumps({"error": "至少需要 2 条记忆才能合并"}, ensure_ascii=False)
        if not merged_content.strip():
            return json.dumps({"error": "合并内容不能为空"}, ensure_ascii=False)

        new_id = await _store.merge_memories(ids, merged_content)
        if not new_id:
            return json.dumps({"error": "合并失败，指定的记忆可能不存在"}, ensure_ascii=False)

        if _embedder and _embedder.available:
            vec = await _embedder.embed_one(merged_content)
            if vec:
                entry = await _store.get(new_id)
                if entry:
                    entry.embedding = vec
                    await _store.update(entry)

        return json.dumps({
            "ok": True, "new_id": new_id,
            "merged_from": ids,
            "message": f"已将 {len(ids)} 条记忆合并为 id={new_id}",
        }, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


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
        return json.dumps({"error": str(e)}, ensure_ascii=False)


# ------------------------------------------------------------------
# 任务执行
# ------------------------------------------------------------------

@deferred_tool(
    group="memory", tags=["core", "heartbeat"], source="mind.memory",
    description="列出所有可执行的任务。任务是预定义的流程化工作，可按名称触发执行。",
)
async def list_tasks() -> str:
    """列出所有可执行的任务。"""
    try:
        from services._runtime import require_runtime
        tasks = require_runtime().mind.heartbeat_engine.task_registry.list_info()
        return json.dumps({"total": len(tasks), "tasks": tasks}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


@deferred_tool(
    group="memory", tags=["core", "heartbeat"], source="mind.memory",
    description=(
        "按名称执行指定任务。任务在后台异步执行，可通过 list_tasks 查看可用任务。"
        "任务执行期间会调用工具完成具体工作（如整理画像、清理记忆等）。"
    ),
)
async def execute_task(task_name: str) -> str:
    """按名称执行指定任务。

    Args:
        task_name: 任务名称（通过 list_tasks 获取）
    """
    try:
        from services._runtime import require_runtime
        rt = require_runtime()
        result = await rt.mind.execute_task(task_name)
        if result is None:
            return json.dumps({"ok": False, "message": f"任务 {task_name} 不存在、已禁用或无产出"}, ensure_ascii=False)
        return json.dumps({
            "ok": True,
            "task": task_name,
            "preview": result[:200] if result else "",
        }, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


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
        return json.dumps({"error": str(e)}, ensure_ascii=False)


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
        scope_id: 用户 ID 或群组 ID
    """
    try:
        sqlite = _get_sqlite()
        primary = await sqlite.resolve_alias(scope_type, scope_id)
        p_type, p_id = primary if primary else (scope_type, scope_id)

        data = await sqlite.get_entity_personality(scope_type=p_type, scope_id=p_id)
        if not data:
            return json.dumps({"error": f"{scope_type}:{scope_id} 暂无画像"}, ensure_ascii=False)

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
        return json.dumps({"error": str(e)}, ensure_ascii=False)


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
        scope_id: 用户 ID 或群组 ID
    """
    try:
        sqlite = _get_sqlite()
        existing = await sqlite.get_entity_personality(scope_type=scope_type, scope_id=scope_id)
        if not existing:
            return json.dumps({"error": f"{scope_type}:{scope_id} 不存在"}, ensure_ascii=False)

        await sqlite.delete_entity_profile(scope_type=scope_type, scope_id=scope_id)

        # 清理内存缓存
        try:
            from services._runtime import require_runtime
            rt = require_runtime()
            key = f"{scope_type}_{scope_id}"
            rt.data_center.everything_data.entities.pop(key, None)
        except Exception:
            pass

        # 清理 MemoryStore 中的 ENTITY 记忆
        if _store:
            source = f"entity_{scope_id}"
            old_entries = await _store.list_recent(
                limit=5, memory_type=MemoryType.ENTITY, source=source,
            )
            for entry in old_entries:
                if entry.id:
                    await _store.delete(entry.id)

        return json.dumps({
            "ok": True,
            "message": f"已删除 {scope_type}:{scope_id} 的画像",
        }, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


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
        scope_id: 用户 ID 或群组 ID
        personality: 新的画像内容（Markdown 格式的结构化描述）
    """
    try:
        if not personality.strip():
            return json.dumps({"error": "画像内容不能为空"}, ensure_ascii=False)

        sqlite = _get_sqlite()
        primary = await sqlite.resolve_alias(scope_type, scope_id)
        p_type, p_id = primary if primary else (scope_type, scope_id)

        old = await sqlite.get_entity_personality(scope_type=p_type, scope_id=p_id)
        conv_num = old.get("conv_num", 0) if old else 0

        await sqlite.set_entity_personality(
            scope_type=p_type, scope_id=p_id, personality=personality.strip(),
            conv_num=conv_num, conv_update_num=0,
        )

        # 同步更新内存缓存（primary + 所有 alias 的内存实体）
        try:
            from services._runtime import require_runtime
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
            pass

        # 同步更新 MemoryStore 中的 ENTITY 记忆
        if _store:
            source = f"entity_{p_id}"
            scope_tag = f"{p_type}:{p_id}"
            old_entries = await _store.list_recent(
                limit=5, memory_type=MemoryType.ENTITY, source=source,
            )
            for old_entry in old_entries:
                if old_entry.id:
                    await _store.delete(old_entry.id)
            entry = MemoryEntry(
                memory_type=MemoryType.ENTITY,
                content=personality.strip(),
                source=source,
                tags=[scope_tag, "type:profile"],
                importance=0.8,
            )
            if _embedder and _embedder.available:
                entry.embedding = await _embedder.embed_one(personality.strip())
            await _store.add(entry)

        target_desc = f"{p_type}:{p_id}"
        if primary:
            target_desc += f" (通过 {scope_type}:{scope_id})"
        return json.dumps({
            "ok": True,
            "message": f"已更新 {target_desc} 的画像",
            "preview": personality.strip()[:100],
        }, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


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
        source_scope_id: 源实体 ID
        target_scope_type: 目标实体类型（user 或 group）
        target_scope_id: 目标实体 ID
    """
    try:
        if source_scope_type == target_scope_type and source_scope_id == target_scope_id:
            return json.dumps({"error": "不能将实体关联到自身"}, ensure_ascii=False)

        sqlite = _get_sqlite()

        # 追踪 target 的最终 primary（避免链式别名）
        target_primary = await sqlite.resolve_alias(target_scope_type, target_scope_id)
        final_type, final_id = target_primary if target_primary else (target_scope_type, target_scope_id)

        # 检查 source 是否已有不同的 primary
        existing = await sqlite.resolve_alias(source_scope_type, source_scope_id)
        if existing and (existing[0] != final_type or existing[1] != final_id):
            return json.dumps({
                "error": (
                    f"{source_scope_type}:{source_scope_id} 已关联到 "
                    f"{existing[0]}:{existing[1]}，需先 unlink_entity 解除"
                ),
            }, ensure_ascii=False)

        await sqlite.set_alias(
            scope_type=source_scope_type, scope_id=source_scope_id,
            primary_scope_type=final_type, primary_scope_id=final_id,
        )

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
        return json.dumps({"error": str(e)}, ensure_ascii=False)


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
        scope_id: 实体 ID
        copy_profile: 是否将当前主身份的画像复制一份给自己（默认 True）
    """
    try:
        sqlite = _get_sqlite()
        primary = await sqlite.resolve_alias(scope_type, scope_id)
        if not primary:
            return json.dumps({
                "error": f"{scope_type}:{scope_id} 没有关联关系",
            }, ensure_ascii=False)

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
            return json.dumps({"error": "解除失败"}, ensure_ascii=False)

        return json.dumps({
            "ok": True,
            "message": (
                f"已解除 {scope_type}:{scope_id} 与 "
                f"{primary[0]}:{primary[1]} 的关联"
            ),
            "copied_profile": copy_profile,
        }, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


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
    if not _store:
        return json.dumps({"error": "记忆系统未初始化"}, ensure_ascii=False)
    try:
        limit = int(limit)
        if not tool_name:
            stats = await _store.get_tool_error_stats()
            return json.dumps({"stats": stats}, ensure_ascii=False)
        errors = await _store.get_tool_errors(tool_name=tool_name, limit=limit)
        return json.dumps({
            "tool_name": tool_name,
            "count": len(errors),
            "errors": errors,
        }, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)
