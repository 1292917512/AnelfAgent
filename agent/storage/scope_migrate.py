"""Scope 迁移：旧格式 scope 键（无 adapter 段）→ 新格式（``{adapter}:{base_id}``）。

多频道下同号实体会碰撞（如 QQ uid=123 与 WebUI uid=123 共用 ``user_123``），
scope_id 因此内嵌 adapter 维度。本模块负责存量数据的一次性迁移：

- 主库（conversation_messages / entity_profile / entity_alias / pending_tasks）
- 记忆库（memories / memories_archive 的 entity 标签与画像 source）

幂等性由 ``PRAGMA user_version`` 保证（两库各自独立标记）；迁移前自动
``VACUUM INTO`` 备份，失败回滚后可凭备份手工恢复。

注意：cognee 投影层的 dataset 名基于 scope_id 哈希（不可逆），迁移后旧
dataset 成为孤儿，由 cognee 同步队列按新键重建，不做迁移。
"""

from __future__ import annotations

import json
import os
from typing import Any

import aiosqlite

from agent.messages import build_entity_scope, parse_entity_scope
from core.config import get_config
from core.log import log

# 两库各自的 user_version 目标值（独立演进，互不共享）
MAIN_SCOPE_MIGRATION_VERSION = 1
MEMORY_TAG_MIGRATION_VERSION = 1

_TAG = "迁移"

# 知名内置频道的固定 uid → adapter 映射：这些 scope 的归属与 legacy 默认无关
# （webui/cli 的历史行 adapter_key 可能为空——旧代码的 assistant 回复不落该列，
# 若统一回落 legacy 默认会把 WebUI 历史撕裂到 qq:web_user）
_KNOWN_SCOPE_ADAPTERS = {"web_user": "webui", "cli_user": "cli"}


def resolve_scope_adapter(scope_base_id: str, row_adapter: str, legacy_adapter: str) -> str:
    """决定一个旧格式 scope 的迁移归属频道。

    优先级：行内/映射表中的 adapter > 知名内置频道启发式 > legacy 默认。
    """
    if row_adapter:
        return row_adapter
    base = scope_base_id.split("#", 1)[0]
    return _KNOWN_SCOPE_ADAPTERS.get(base, legacy_adapter)


def get_legacy_adapter() -> str:
    """存量无频道信息数据迁移时归属的默认 adapter key。"""
    return str(get_config("legacy_adapter_default", "qq") or "qq")


def is_alias_merge_enabled() -> bool:
    """读取对话历史时是否合并别名实体的跨频道历史。"""
    from core.config import get_config_bool
    return get_config_bool("alias_merge_history", True)


def rewrite_entity_tag(tag: Any, legacy_adapter: str) -> Any:
    """把 ``user:X`` / ``group:X`` 旧实体标签重写为 ``user:{adapter}:X`` 三段式。

    归属频道按知名内置 scope 启发式修正（user:web_user → user:webui:web_user），
    其余回落 legacy 默认；已是三段式或非实体标签时原样返回。
    """
    if not isinstance(tag, str):
        return tag
    for head in ("user:", "group:"):
        if tag.startswith(head):
            rest = tag[len(head):]
            if rest and ":" not in rest:
                adapter = resolve_scope_adapter(rest, "", legacy_adapter)
                return f"{head}{adapter}:{rest}"
    return tag


def rewrite_profile_source(source: Any, legacy_adapter: str) -> Any:
    """把画像记忆 source ``entity_X`` 重写为 ``entity_{adapter}:X``（与 scope_id 对齐）。"""
    if not isinstance(source, str) or not source.startswith("entity_"):
        return source
    rest = source[len("entity_"):]
    if rest and ":" not in rest:
        adapter = resolve_scope_adapter(rest, "", legacy_adapter)
        return f"entity_{adapter}:{rest}"
    return source


async def _user_version(db: aiosqlite.Connection) -> int:
    cursor = await db.execute("PRAGMA user_version")
    row = await cursor.fetchone()
    return int(row[0]) if row else 0


async def _backup_db(db: aiosqlite.Connection, db_path: str, suffix: str) -> None:
    """迁移前整库备份（VACUUM INTO 快照；已存在则不覆盖，保留最早的原始态）。"""
    backup_path = f"{db_path}{suffix}"
    if os.path.exists(backup_path):
        return
    escaped = backup_path.replace("'", "''")
    await db.execute(f"VACUUM INTO '{escaped}'")
    log(f"迁移备份: {backup_path}", tag=_TAG)


async def _migrate_pending_tasks(db: aiosqlite.Connection, legacy_adapter: str) -> int:
    """逐行重写 pending_tasks 的 scope 列与 payload_json.scope 字段。"""
    cursor = await db.execute("SELECT id, scope, payload_json FROM pending_tasks")
    rows = await cursor.fetchall()
    migrated = 0
    for row_id, scope, payload_json in rows:
        new_scope = scope
        scope_type, adapter, base_id, session_id = parse_entity_scope(scope or "")
        if scope_type and not adapter:
            new_scope = build_entity_scope(scope_type, legacy_adapter, base_id, session_id)

        new_payload = payload_json
        try:
            payload: dict = json.loads(payload_json or "{}")
        except (json.JSONDecodeError, TypeError):
            payload = {}
        if isinstance(payload, dict):
            p_scope = payload.get("scope")
            if isinstance(p_scope, str) and p_scope:
                p_type, p_adapter, p_base, p_session = parse_entity_scope(p_scope)
                if p_type and not p_adapter:
                    # payload 自带 adapter_key 优先，知名内置 scope 启发式其次，全局默认兜底
                    effective = resolve_scope_adapter(
                        p_base, str(payload.get("adapter_key") or ""), legacy_adapter
                    )
                    payload["scope"] = build_entity_scope(p_type, effective, p_base, p_session)
                    new_payload = json.dumps(payload, ensure_ascii=False, default=str)

        if new_scope != scope or new_payload != payload_json:
            await db.execute(
                "UPDATE pending_tasks SET scope=?, payload_json=? WHERE id=?",
                (new_scope, new_payload, row_id),
            )
            migrated += 1
    return migrated


async def _conversation_scope_adapters(db: aiosqlite.Connection) -> dict:
    """扫描会话表，推断每个旧格式 scope 的归属频道（同 scope 最新非空 adapter 优先）。

    旧代码的 assistant 回复不落 adapter_key 列，按行回落会把同一对话撕裂到
    不同频道（如 WebUI 的用户消息在 webui:*、回复被错挂到 qq:*）——
    因此空值行按同 scope 的既有频道归属，保证对话完整。
    """
    cursor = await db.execute(
        "SELECT scope_type, scope_id, adapter_key FROM conversation_messages "
        "WHERE adapter_key != '' ORDER BY ts_ns"
    )
    mapping: dict[tuple[str, str], str] = {}
    for scope_type, scope_id, adapter_key in await cursor.fetchall():
        # 按 ts 升序遍历，后写入的（更新的）覆盖——以最近一条的频道为准
        mapping[(scope_type, scope_id)] = adapter_key
    return mapping


async def migrate_main_db_scopes(
    db: aiosqlite.Connection, db_path: str, legacy_adapter: str
) -> bool:
    """主库 scope 键迁移（conversation/profile/alias/pending_tasks）。

    幂等：user_version >= 目标版本时跳过。归属优先级：
    行内 adapter_key > 同 scope 最新非空 adapter > 知名内置 scope 启发式 > legacy 默认。
    """
    if not legacy_adapter:
        return False
    if await _user_version(db) >= MAIN_SCOPE_MIGRATION_VERSION:
        return False

    await _backup_db(db, db_path, ".pre-scope-migration.bak")
    try:
        # 会话表：scope 级归属映射（空 adapter 行跟随同 scope 的既有频道）
        scope_adapters = await _conversation_scope_adapters(db)
        cursor = await db.execute(
            "SELECT DISTINCT scope_type, scope_id FROM conversation_messages "
            "WHERE instr(scope_id, ':') = 0"
        )
        conv_count = 0
        for scope_type, scope_id in await cursor.fetchall():
            base_id = scope_id.split("#", 1)[0]
            adapter = resolve_scope_adapter(
                base_id, scope_adapters.get((scope_type, scope_id), ""), legacy_adapter,
            )
            cur = await db.execute(
                "UPDATE conversation_messages SET scope_id = ? || ':' || scope_id "
                "WHERE scope_type = ? AND scope_id = ?",
                (adapter, scope_type, scope_id),
            )
            conv_count += cur.rowcount

        # 画像/别名：无频道信息，按知名内置 scope 启发式 + legacy 默认
        cursor = await db.execute(
            "SELECT scope_type, scope_id FROM entity_profile WHERE instr(scope_id, ':') = 0"
        )
        for scope_type, scope_id in await cursor.fetchall():
            adapter = resolve_scope_adapter(scope_id, "", legacy_adapter)
            await db.execute(
                "UPDATE entity_profile SET scope_id = ? || ':' || scope_id "
                "WHERE scope_type = ? AND scope_id = ?",
                (adapter, scope_type, scope_id),
            )
        cursor = await db.execute(
            "SELECT scope_type, scope_id, primary_scope_type, primary_scope_id "
            "FROM entity_alias WHERE instr(scope_id, ':') = 0"
        )
        for scope_type, scope_id, _p_type, p_id in await cursor.fetchall():
            adapter = resolve_scope_adapter(scope_id, "", legacy_adapter)
            p_adapter = resolve_scope_adapter(p_id, "", legacy_adapter)
            await db.execute(
                "UPDATE entity_alias SET scope_id = ? || ':' || scope_id, "
                "primary_scope_id = ? || ':' || primary_scope_id "
                "WHERE scope_type = ? AND scope_id = ?",
                (adapter, p_adapter, scope_type, scope_id),
            )

        pending_count = await _migrate_pending_tasks(db, legacy_adapter)
        await db.execute(f"PRAGMA user_version = {MAIN_SCOPE_MIGRATION_VERSION}")
        await db.commit()
    except Exception:
        await db.rollback()
        raise
    log(
        f"主库 scope 迁移完成: 会话 {conv_count} 行, 待办 {pending_count} 行"
        f"（legacy={legacy_adapter}）",
        tag=_TAG,
    )
    return True


async def migrate_memory_db_tags(
    db: aiosqlite.Connection, db_path: str, legacy_adapter: str
) -> bool:
    """记忆库实体标签与画像 source 迁移（memories / memories_archive）。"""
    if not legacy_adapter:
        return False
    if await _user_version(db) >= MEMORY_TAG_MIGRATION_VERSION:
        return False

    await _backup_db(db, db_path, ".pre-scope-migration.bak")
    migrated = 0
    try:
        for table in ("memories", "memories_archive"):
            cursor = await db.execute(f"SELECT id, tags_json, source FROM {table}")
            rows = await cursor.fetchall()
            for row_id, tags_json, source in rows:
                try:
                    tags = json.loads(tags_json or "[]")
                except (json.JSONDecodeError, TypeError):
                    continue
                if not isinstance(tags, list):
                    continue
                new_tags = [rewrite_entity_tag(t, legacy_adapter) for t in tags]
                new_source = rewrite_profile_source(source, legacy_adapter)
                if new_tags != tags or new_source != source:
                    await db.execute(
                        f"UPDATE {table} SET tags_json=?, source=? WHERE id=?",
                        (json.dumps(new_tags, ensure_ascii=False), new_source or "", row_id),
                    )
                    migrated += 1
        await db.execute(f"PRAGMA user_version = {MEMORY_TAG_MIGRATION_VERSION}")
        await db.commit()
    except Exception:
        await db.rollback()
        raise
    log(f"记忆库标签迁移完成: {migrated} 行（legacy={legacy_adapter}）", tag=_TAG)
    return True


# ------------------------------------------------------------------
# 配置注册
# ------------------------------------------------------------------

_SCOPE_MIGRATION_CONFIGS = {
    "Scope迁移": {
        "legacy_adapter_default": {
            "description": "存量无频道信息的数据（历史消息/画像/记忆标签）迁移时归属的默认频道 adapter key",
            "default": "qq",
        },
        "alias_merge_history": {
            "description": "读取对话历史时是否合并别名实体的跨频道历史（认出同一个人）",
            "default": True,
        },
    },
}

from core.config import register_configs_safe  # noqa: E402

register_configs_safe(_SCOPE_MIGRATION_CONFIGS)
