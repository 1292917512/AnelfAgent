"""实体画像备份：覆盖更新前自动留存旧画像（防 LLM 写出坏画像不可恢复）。

画像是唯一权威的覆盖式存储，一次错误的重写就会丢失全部历史描述。
每次覆盖前把旧内容写入 config/memory/profile_backups/，每实体保留最近
3 份（文件名带时间戳，便于人工恢复或 diff）。
"""

from __future__ import annotations

import time
from pathlib import Path

from core.log import log

_KEEP_PER_ENTITY = 3


def _backup_dir() -> Path:
    from agent.memory.notes import get_memory_dir
    path = get_memory_dir() / "profile_backups"
    path.mkdir(parents=True, exist_ok=True)
    return path


def backup_entity_profile(scope_type: str, scope_id: str, personality: str) -> bool:
    """覆盖更新前备份旧画像内容；无旧内容时不产生备份。返回是否备份成功。"""
    personality = (personality or "").strip()
    if not personality:
        return False
    try:
        safe_id = "".join(c if c.isalnum() or c in "-_:" else "_" for c in scope_id)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        path = _backup_dir() / f"{scope_type}_{safe_id}.{stamp}.md"
        path.write_text(personality, encoding="utf-8")

        # 滚动保留：每实体只留最近 _KEEP_PER_ENTITY 份
        backups = sorted(_backup_dir().glob(f"{scope_type}_{safe_id}.*.md"))
        for old in backups[:-_KEEP_PER_ENTITY]:
            old.unlink(missing_ok=True)
        log(f"画像备份: {scope_type}:{scope_id} → {path.name}", "DEBUG", tag="记忆")
        return True
    except Exception as exc:
        log(f"画像备份失败 {scope_type}:{scope_id}: {exc}", "WARNING", tag="记忆")
        return False
