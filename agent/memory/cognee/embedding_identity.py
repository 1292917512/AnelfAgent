"""数据卷的向量空间身份：兼容已有投影，阻止换模型后混写旧向量。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from core.file_utils import atomic_write_text

from .llm_bridge import CogneeConfigError

_FILENAME = "anelf-embedding.json"


def embedding_identity(payload: dict[str, Any]) -> dict[str, Any]:
    """只记录影响向量空间的设置，不保存凭据、批量大小或生成参数。"""
    provider = str(payload.get("embedding_provider") or "")
    model = str(payload.get("embedding_model") or "")
    model = model.removeprefix(f"{provider}/")
    return {
        "provider": provider,
        "model": model,
        "endpoint": str(payload.get("embedding_endpoint") or "").rstrip("/"),
        "dimensions": int(payload.get("embedding_dimensions") or 0),
    }


def validate_embedding(root: Path, payload: dict[str, Any], *, record: bool = False) -> None:
    """旧数据首次沿用当前配置登记身份；以后变更必须使用独立卷或显式重建。"""
    path = root / _FILENAME
    desired = embedding_identity(payload)
    previous: dict[str, Any] = {}
    if path.exists():
        try:
            previous = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise CogneeConfigError("Cognee 向量身份文件不可读，请修复后继续使用原数据") from exc
        if not isinstance(previous, dict):
            raise CogneeConfigError("Cognee 向量身份文件格式无效")
        changed = any(previous.get(key) != desired[key] for key in ("provider", "model", "endpoint"))
        old_dims = previous.get("dimensions", 0)
        new_dims = desired["dimensions"]
        changed = changed or bool(old_dims and new_dims and old_dims != new_dims)
        if changed:
            raise CogneeConfigError(
                "所选 Embedding 与该 Cognee 数据卷不兼容，已保留旧数据。"
                "请保持原模型，或选择新的数据目录；确需替换时再显式清空重建并重启。"
                "修改聊天模型、输出上限或召回参数无需重建。"
            )
        if not new_dims and old_dims:
            desired["dimensions"] = old_dims
    if record and (not path.exists() or previous != desired):
        root.mkdir(parents=True, exist_ok=True)
        atomic_write_text(path, json.dumps(desired, ensure_ascii=False, indent=2))


def clear_embedding_identity(root: Path) -> None:
    """仅在用户显式清空整个 Cognee 数据卷成功后移除身份记录。"""
    (root / _FILENAME).unlink(missing_ok=True)
