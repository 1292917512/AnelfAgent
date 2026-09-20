"""数据库管理共享原语 — database / db_connections / volume_ops 共用的叶子模块。

放置跨模块契约（错误类型 / 影子表模式 / 值序列化），使 db_connections
不再反向依赖 database（依赖方向：database → db_connections 单向）。
"""

from __future__ import annotations

import array
import json
import time
from typing import Any, Dict

from core.log import log

CELL_TEXT_MAX = 500  # 浏览时单元格文本截断长度（全文走单行详情接口）

# FTS5 / vec0 影子表（内部维护，默认不出现在表清单）
SHADOW_PATTERNS = (
    "_fts_data",
    "_fts_idx",
    "_fts_docsize",
    "_fts_config",
    "_fts_content",
    "_vec_chunks",
    "_vec_rowids",
    "_vec_vector_chunks",
    "_vec_info",
)


class DatabaseError(RuntimeError):
    """数据库管理操作错误（router 转成 HTTPException）。"""

    def __init__(self, message: str, *, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


def looks_like_embedding_column(column: str) -> bool:
    name = column.lower()
    return "embedding" in name or name.endswith("_vec") or "vector" in name


def serialize_value(value: Any, column: str) -> Any:
    """把 SQLite 值转成 JSON 可序列化的智能结构。"""
    if value is None:
        return None
    if isinstance(value, bytes):
        info: Dict[str, Any] = {"__type__": "blob", "bytes": len(value)}
        # float32 小端向量（embedding_blob / embedding 列）
        if len(value) >= 4 and len(value) % 4 == 0 and looks_like_embedding_column(column):
            try:
                arr = array.array("f")
                arr.frombytes(value[: 4 * 4])  # 预览前 4 维
                info["__type__"] = "vec"
                info["dims"] = len(value) // 4
                info["preview"] = [round(float(x), 4) for x in arr]
            except Exception:
                log("serialize_value 异常已忽略", "DEBUG")
        return info
    if isinstance(value, (int, float)):
        # *_ns 纳秒时间戳列 → 附可读时间
        if column.endswith("_ns") and isinstance(value, int) and value > 10**15:
            return {
                "__type__": "ts",
                "value": value,
                "text": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(value / 1e9)),
            }
        return value
    text = str(value)
    stripped = text.strip()
    if stripped.startswith(("{", "[")):
        try:
            parsed = json.loads(stripped)
            out: Dict[str, Any] = {
                "__type__": "json",
                "value": parsed,
                "raw": text if len(text) <= CELL_TEXT_MAX else text[:CELL_TEXT_MAX],
            }
            if len(text) > CELL_TEXT_MAX:
                out["truncated"] = True
            return out
        except (ValueError, TypeError):
            log("serialize_value 异常已忽略", "DEBUG")
    if len(text) > CELL_TEXT_MAX:
        return {"__type__": "text", "text": text[:CELL_TEXT_MAX], "truncated": True}
    return text
