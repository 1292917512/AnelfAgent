"""Embedding 调用用量统计：日级账本（调用数 / 文本条数 / 输入字符数）。

主 Embedder 引擎的唯一埋点（查询 / 批量 / 多模态 / probe 全覆盖），
内存累加 + 防抖落盘，重启后经 JSON 快照恢复。cognee 走自带引擎不经
本模块，其消耗以供应商控制台为最终对账口径。
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict

from core.log import log
from core.storage_volume import main_sqlite_path

_FLUSH_EVERY = 20  # 每积累多少条记录落盘一次
_KEEP_DAYS = 90

_state: Dict[str, Dict[str, int]] = {}
_pending = 0
_loaded = False


def _path() -> Path:
    return Path(main_sqlite_path()).parent / "embedding_usage.json"


def _load() -> None:
    global _loaded, _state
    if _loaded:
        return
    _loaded = True
    try:
        raw = json.loads(_path().read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            _state = {
                str(day): {k: int(vv) for k, vv in value.items()}
                for day, value in raw.items()
                if isinstance(value, dict)
            }
    except (OSError, ValueError, AttributeError):
        _state = {}


def record_embedding_call(*, texts: int, chars: int, calls: int = 1) -> None:
    """记录一次 embedding API 调用（引擎埋点，永不抛异常）。"""
    global _pending
    try:
        _load()
        day = time.strftime("%Y-%m-%d")
        row = _state.setdefault(day, {"calls": 0, "texts": 0, "chars": 0})
        row["calls"] += calls
        row["texts"] += texts
        row["chars"] += chars
        _pending += 1
        if _pending >= _FLUSH_EVERY:
            flush_embedding_usage()
    except Exception as exc:
        log(f"embedding 用量记录失败: {exc}", "DEBUG")


def flush_embedding_usage() -> None:
    """把内存账本落盘（worker 关闭与防抖阈值处调用）。"""
    global _pending, _state
    _load()
    if _pending == 0:
        return
    _pending = 0
    try:
        days = sorted(_state)[-_KEEP_DAYS:]
        _state = {day: _state[day] for day in days}
        path = _path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(".tmp")
        tmp_path.write_text(json.dumps(_state, ensure_ascii=False), encoding="utf-8")
        tmp_path.replace(path)
    except OSError as exc:
        log(f"embedding 用量落盘失败: {exc}", "DEBUG")


def embedding_usage_summary(days: int = 14) -> Dict[str, Any]:
    """最近 N 天的日级明细与合计（chars 为输入字符数，token 以供应商口径为准）。"""
    _load()
    recent_days = sorted(_state)[-max(1, days):]
    daily = [
        {
            "date": day,
            "calls": _state[day].get("calls", 0),
            "texts": _state[day].get("texts", 0),
            "chars": _state[day].get("chars", 0),
        }
        for day in recent_days
    ]
    return {
        "daily": daily,
        "totals": {
            "calls": sum(row["calls"] for row in daily),
            "texts": sum(row["texts"] for row in daily),
            "chars": sum(row["chars"] for row in daily),
        },
        "note": "仅统计主 Embedder 引擎（记忆/召回/技能/贴纸等）；"
                "cognee 自带引擎的调用不在此口径内，token 数以供应商控制台为准",
    }
