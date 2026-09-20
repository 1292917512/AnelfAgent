"""会话消息工具 — 对话历史的查阅 / 精确取回 / 追加 / 修改 / 清空 / 语义检索。

与 agent.memory.tools（长期记忆域）同包不同域：本会话消息存储（sqlite
conversation 表）的 AI 工具面；sqlite 访问与 scope 归一化复用 tools 的
共享辅助，embedding 经 memory_tools_port 晚绑定获取。
"""

from __future__ import annotations

import json

from core.log import log
from core.tool_errors import ErrorCause, error_from_exception, tool_error
from entities._sdk import deferred_tool

from .embedding import wake_embedding_worker
from .tools import _deps, _get_sqlite, _normalize_scope_id


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
        return error_from_exception(e, action="列出会话")


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
        scope_id: 用户 ID 或群组 ID（格式 ``{频道}:{id}``，如 qq:123；传裸 id 时按当前会话频道解析）
        limit: 最大返回条数，默认 30，最大 100
    """
    try:
        sqlite = _get_sqlite()
        limit = max(1, min(limit, 100))
        scope_id = _normalize_scope_id(scope_id)
        records = await sqlite.fetch_conversation_with_id(
            scope_type=scope_type, scope_id=scope_id, limit=limit,
        )
        items = []
        for r in records:
            items.append({
                "id": r["id"],
                "role": r["role"],
                "content": r["content"],
                "time": _format_conversation_time(int(r["ts_ns"])),
            })
        return json.dumps({
            "scope": f"{scope_type}:{scope_id}",
            "count": len(items),
            "messages": items,
        }, ensure_ascii=False)
    except Exception as e:
        return error_from_exception(e, action="获取会话记录")


def _format_conversation_time(ts_ns: int) -> str:
    """格式化纳秒时间戳为可读时间（对话工具统一格式）。"""
    import datetime
    return datetime.datetime.fromtimestamp(ts_ns // 1_000_000_000).strftime("%Y-%m-%d %H:%M:%S")




def _resolve_lookup_scope(
    scope_type: str,
    scope_id: str,
) -> tuple[str, str]:
    """解析查找 scope：显式参数优先，否则回落到当前对话 scope。"""
    st = (scope_type or "").strip()
    sid = _normalize_scope_id(scope_id)
    if st and sid:
        return st, sid
    try:
        from agent.mind.tool_activation import ToolActivationManager
        scope = ToolActivationManager.current_scope()
        if scope.startswith("user_"):
            return "user", scope[5:]
        if scope.startswith("group_"):
            return "group", scope[6:]
    except Exception:
        log("_resolve_lookup_scope 异常已忽略", "DEBUG")
    return "", ""


@deferred_tool(
    group="memory", tags=["core", "heartbeat", "always"], source="mind.memory",
    description=(
        "按 message_id 精确查找会话中的某条消息（含窗口外历史）。"
        "当看到 [reply_to:xxx] 需要原文、或 [message_id:xxx] 需要定位该条时使用；"
        "可返回前后邻接消息便于理解上下文。"
        "与 recall_conversation（语义搜）互补：本工具按 ID 精确取回。"
    ),
)
async def lookup_message(
    message_id: str,
    scope_type: str = "",
    scope_id: str = "",
    context_before: int = 2,
    context_after: int = 2,
) -> str:
    """按 ``[message_id:xxx]`` 精确查找对话消息（含窗口外历史）。

    典型用法：当前消息带 ``[reply_to:abc]`` 且预览不够用时，
    传入 ``message_id=abc`` 取回被引用原文；也可直接用任意已知的 message_id。

    Args:
        message_id: 平台消息 ID（来自 [message_id:xxx] 或 [reply_to:xxx]）
        scope_type: 可选，user 或 group；为空则优先当前会话，仍无则跨会话搜索
        scope_id: 可选，用户 ID 或群组 ID（格式 ``{频道}:{id}``，如 qq:123；传裸 id 时按当前会话频道解析）
        context_before: 一并返回该消息之前的邻接条数，默认 2，最大 10
        context_after: 一并返回该消息之后的邻接条数，默认 2，最大 10
    """
    try:
        message_id = (message_id or "").strip()
        if not message_id:
            return tool_error("message_id 不能为空", cause=ErrorCause.PARAM, retryable=False)

        sqlite = _get_sqlite()
        st, sid = _resolve_lookup_scope(scope_type, scope_id)
        context_before = max(0, min(int(context_before), 10))
        context_after = max(0, min(int(context_after), 10))

        # 先在当前/指定 scope 内精确查；未命中且未强制指定 scope 时再跨会话兜底
        hits = await sqlite.find_conversation_by_message_id(
            message_id, scope_type=st, scope_id=sid, limit=5,
        )
        searched_global = False
        if not hits and not ((scope_type or "").strip() and (scope_id or "").strip()):
            hits = await sqlite.find_conversation_by_message_id(
                message_id, limit=5,
            )
            searched_global = True

        if not hits:
            return json.dumps({
                "found": False,
                "message_id": message_id,
                "scope": f"{st}:{sid}" if st and sid else "",
                "searched_global": searched_global or not (st and sid),
                "message": (
                    "未找到该 message_id 对应的会话记录。"
                    "可能原因：原消息未入库（非本 Bot 可见会话）、"
                    "来自其他平台且 ID 未写入对话、或已被清空。"
                ),
            }, ensure_ascii=False)

        primary = hits[0]
        around = await sqlite.fetch_conversation_around(
            scope_type=primary["scope_type"],
            scope_id=primary["scope_id"],
            center_ts_ns=int(primary["ts_ns"]),
            center_id=int(primary["id"]),
            before=context_before,
            after=context_after,
        )

        def _item(row: dict, *, is_target: bool = False) -> dict:
            item = {
                "id": row["id"],
                "role": row["role"],
                "content": row["content"],
                "time": _format_conversation_time(int(row["ts_ns"])),
            }
            if is_target:
                item["is_target"] = True
                item["message_id"] = primary.get("message_id", message_id)
                if primary.get("reply_to"):
                    item["reply_to"] = primary["reply_to"]
            return item

        context_items = [
            _item(row, is_target=(row["id"] == primary["id"]))
            for row in around
        ]

        return json.dumps({
            "found": True,
            "message_id": message_id,
            "scope": f"{primary['scope_type']}:{primary['scope_id']}",
            "match_count": len(hits),
            "searched_global": searched_global,
            "target": _item(primary, is_target=True),
            "context": context_items,
            "hint": (
                "target 为精确命中的消息；context 含前后邻接。"
                "若只需语义相关历史而非精确 ID，改用 recall_conversation。"
            ),
        }, ensure_ascii=False)
    except Exception as e:
        return error_from_exception(e, action="查找消息")


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
        scope_id: 用户 ID 或群组 ID（格式 ``{频道}:{id}``，如 qq:123；传裸 id 时按当前会话频道解析）
        role: 消息角色（user 或 assistant）
        content: 消息内容
    """
    try:
        if role not in ("user", "assistant"):
            return tool_error("role 必须是 user 或 assistant", cause=ErrorCause.PARAM, retryable=False)
        if not content.strip():
            return tool_error("content 不能为空", cause=ErrorCause.PARAM, retryable=False)
        sqlite = _get_sqlite()
        scope_id = _normalize_scope_id(scope_id)
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
        return error_from_exception(e, action="追加会话消息")


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
            return tool_error("new_content 不能为空", cause=ErrorCause.PARAM, retryable=False)
        sqlite = _get_sqlite()
        updated = await sqlite.update_conversation_message(row_id, new_content)
        if not updated:
            return tool_error(f"消息 {row_id} 不存在", cause=ErrorCause.NOT_FOUND, retryable=False)
        return json.dumps({
            "ok": True,
            "message": f"消息 {row_id} 已更新",
            "new_content_preview": new_content[:100],
        }, ensure_ascii=False)
    except Exception as e:
        return error_from_exception(e, action="更新会话消息")


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
        return error_from_exception(e, action="列出频道")


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
        return error_from_exception(e, action="删除会话消息")


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
        scope_id: 用户 ID 或群组 ID（格式 ``{频道}:{id}``，如 qq:123；传裸 id 时按当前会话频道解析）
    """
    try:
        count = await _get_sqlite().clear_conversation(
            scope_type=scope_type, scope_id=_normalize_scope_id(scope_id),
        )
        return json.dumps({"ok": True, "cleared": count, "message": f"已清空 {scope_type}:{scope_id} 的 {count} 条记录"}, ensure_ascii=False)
    except Exception as e:
        return error_from_exception(e, action="清空会话记录")


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
        scope_id: 用户 ID 或群组 ID（格式 ``{频道}:{id}``，如 qq:123；传裸 id 时按当前会话频道解析）（可通过 list_conversations 查看）
        limit: 最大返回条数，默认 5，最大 10
    """
    import time as _time

    try:
        from agent.config import get_config_provider
        _cfg = get_config_provider().mind
        max_results: int = _cfg.conv_recall_max_results
        min_score: float = _cfg.conv_recall_min_score
        scan_limit: int = _cfg.conv_recall_scan_limit
        skip_recent: int = get_config_provider().config.max_conversation_size
    except Exception as e:
        from core.log import log as _log
        _log(f"对话检索配置加载失败，使用默认值: {e}", "DEBUG")
        max_results, min_score, scan_limit, skip_recent = 10, 0.25, 500, 30

    try:
        sqlite = _get_sqlite()
        limit = max(1, min(limit, max_results))
        scope_id = _normalize_scope_id(scope_id)

        # 通知后台 worker 补齐缺失的 embedding（不阻塞本次检索）
        wake_embedding_worker()

        t0 = _time.monotonic()
        results: list[dict] = []

        deps = _deps()
        if deps is not None and deps.embedder:
            query_vec = await deps.embedder.embed_query(query)
            if query_vec:
                results = await sqlite.search_conversation_vector(
                    scope_type, scope_id, query_vec,
                    limit=limit, skip_recent=skip_recent,
                    min_score=min_score, scan_limit=scan_limit,
                )

        # embedding 不可用或无结果时降级为关键词搜索（jieba 词级切分，
        # 中文对话无空格，按空格拆词基本拿不到有效关键词）
        if not results:
            from .store.tokenizer import tokenize_for_query
            keywords = tokenize_for_query(query)[:8]
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
            items.append({
                "role": r["role"],
                "content": r["content"],
                "time": _format_conversation_time(int(r["ts_ns"])),
                "score": round(r.get("score", 0.0), 3),
            })

        return json.dumps({
            "count": len(items),
            "results": items,
            "elapsed_ms": elapsed_ms,
        }, ensure_ascii=False)

    except Exception as e:
        return error_from_exception(e, action="搜索历史对话")
