"""委托运行日志 — 进度流 / 交接 transcript / 崩溃账本，统一落 <data_dir>/delegations/。

三类持久物，三种消费面：

- **进度流** ``<id>.log``：委托执行期间的轮次/工具事件行 + 最终产出。经
  ``BackgroundTaskRegistry.attach_output_file`` 接入既有增量读取管线
  （``check_background_tasks(task_id=...)`` 单游标消费型读取），父 AI 对
  长委托不再只能看 elapsed 干等——与后台 shell 的输出读取完全同构。
- **transcript** ``<id>.json``：一次委托的完整消息链 + 执行面快照，
  ``follow_up_agent`` 以它为 base_messages 续跑（无损续聊，替代"把有损
  总结当 context 重新委托"）。消息链超上限时不落 messages（档案保留、
  标记不可续跑），绝不截断出半截上下文。
- **账本** ``ledger.jsonl``：每个委托 start/close 各一行。进程崩溃后
  启动扫描未闭合条目 → 注入"上次被中断"元消息（recovery.py 消费），
  扫描时闭合，同一条中断最多通知一次。

按保留天数清理进度与 transcript，并按闭合时间原子压缩账本，保留所有未结委托。
追加账本时触发清理，同一目录最多每十分钟执行一次。写入错误记录日志，委托继续执行。
"""

from __future__ import annotations

import json
import math
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.config import get_config_int
from core.file_utils import atomic_binary_writer, atomic_write_text, file_write_lock
from core.log import log
from core.path import ConfigPaths
from core.sanitizer import sanitize_text
from core.tool_results import redact_tool_messages

# transcript 消息链的序列化上限（超出则不落 messages，标记不可续跑）
TRANSCRIPT_MAX_BYTES = 262_144
_purge_after: dict[Path, float] = {}

# 账本事件类型（读取方按词消费）
LEDGER_STARTED = "started"
LEDGER_CLOSED = "closed"

# 委托终态码（账本 status 字段与 /delegations/history 对外契约）
STATUS_SUCCESS = "success"
STATUS_FAILED = "failed"
STATUS_CANCELLED = "cancelled"
STATUS_LOST = "lost"

_TERMINAL_LABELS = {
    STATUS_SUCCESS: "成功",
    STATUS_FAILED: "失败",
    STATUS_CANCELLED: "已取消",
    STATUS_LOST: "中断",
}


def terminal_status(*, success: bool, cancelled: bool) -> str:
    """由执行结果推导委托终态码。"""
    if cancelled:
        return STATUS_CANCELLED
    return STATUS_SUCCESS if success else STATUS_FAILED


def terminal_label(status: str) -> str:
    """终态码的人类可读标签（进度流与通知文案用）。"""
    return _TERMINAL_LABELS.get(status, status)


def delegation_dir() -> Path:
    return Path(ConfigPaths.DELEGATION_DIR)


def progress_path(delegation_id: str) -> Path:
    return delegation_dir() / f"{delegation_id}.log"


def transcript_path(delegation_id: str) -> Path:
    return delegation_dir() / f"{delegation_id}.json"


def ledger_path() -> Path:
    return delegation_dir() / "ledger.jsonl"


def _retention_days() -> int:
    return max(0, get_config_int("delegation_journal_retention_days", 7))


# ------------------------------------------------------------------
# 进度流
# ------------------------------------------------------------------

def append_progress(delegation_id: str, line: str) -> None:
    """进度流追加一行（带时间戳前缀；fail-open）。"""
    if not delegation_id or not line:
        return
    try:
        path = progress_path(delegation_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%H:%M:%S")
        with path.open("a", encoding="utf-8") as fp:
            fp.write(f"[{stamp}] {line}\n")
    except OSError as exc:
        log(f"委托进度流写入失败（已忽略）: {delegation_id}: {exc}", "DEBUG", tag="委托")


# 进度流尾部读取的字节上限（超大文件只回读尾部，不整文件载入）
_PROGRESS_TAIL_MAX_BYTES = 65_536


def read_progress_tail(delegation_id: str, max_lines: int = 200) -> Dict[str, Any]:
    """读取委托进度流尾部行（面板进度查看用；不存在返回空，fail-open）。"""
    result: Dict[str, Any] = {"lines": [], "truncated": False}
    if not delegation_id:
        return result
    path = progress_path(delegation_id)
    try:
        size = path.stat().st_size
        with path.open("rb") as fp:
            if size > _PROGRESS_TAIL_MAX_BYTES:
                fp.seek(-_PROGRESS_TAIL_MAX_BYTES, 2)
                raw = fp.read()
                result["truncated"] = True
            else:
                raw = fp.read()
        text = raw.decode("utf-8", errors="replace")
        lines = text.splitlines()
        if result["truncated"] and lines:
            # 尾部回读的首行可能是半行，丢弃
            lines = lines[1:]
        if max_lines > 0 and len(lines) > max_lines:
            lines = lines[-max_lines:]
            result["truncated"] = True
        result["lines"] = lines
    except OSError:
        pass
    return result


# ------------------------------------------------------------------
# transcript（续跑数据源）
# ------------------------------------------------------------------

def save_transcript(data: Dict[str, Any]) -> bool:
    """持久化委托 transcript（原子写；消息链超上限降级为不可续跑档案）。"""
    delegation_id = str(data.get("delegation_id", ""))
    if not delegation_id:
        return False
    try:
        payload = dict(data)
        if isinstance(messages := payload.get("messages"), list):
            payload["messages"] = redact_tool_messages(messages)
        encoded = json.dumps(payload, ensure_ascii=False)
        if len(encoded.encode("utf-8")) > TRANSCRIPT_MAX_BYTES:
            payload["messages"] = None
            payload["continuable"] = False
            encoded = json.dumps(payload, ensure_ascii=False)
        path = transcript_path(delegation_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(path, encoded)
        return True
    except Exception as exc:
        log(f"委托 transcript 保存失败（已忽略）: {exc}", "DEBUG", tag="委托")
        return False


def load_transcript(delegation_id: str) -> Optional[Dict[str, Any]]:
    """读取委托 transcript（不存在/损坏/不可续跑返回 None）。"""
    path = transcript_path(delegation_id)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict) or not data.get("messages"):
        return None
    return data


# ------------------------------------------------------------------
# 账本（崩溃恢复数据源）
# ------------------------------------------------------------------

def append_ledger(event: str, delegation_id: str, **fields: Any) -> bool:
    """账本追加一行事件，返回写入结果，失败时委托可以继续。"""
    try:
        path = ledger_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "event": event,
            "id": delegation_id,
            "ts": round(time.time(), 3),
            **fields,
        }
        for key in ("goal", "summary"):
            if isinstance(record.get(key), str):
                record[key] = sanitize_text(record[key])
        line = (json.dumps(record, ensure_ascii=False) + "\n").encode("utf-8")
        with file_write_lock(path):
            with path.open("a+b") as fp:
                if fp.tell():
                    fp.seek(-1, 2)
                    if fp.read(1) != b"\n":
                        fp.write(b"\n")
                fp.write(line)
            _purge_expired()
        return True
    except OSError as exc:
        log(f"委托账本写入失败（已忽略）: {delegation_id}: {exc}", "DEBUG", tag="委托")
        return False


def _ledger_records(path: Path, tail_bytes: int = 0) -> Iterator[tuple[int, Dict[str, Any]]]:
    """逐行解析；尾读从完整行边界开始，跳过截断、编码错误或非对象行。"""
    with path.open("rb") as fp:
        if tail_bytes and fp.seek(0, 2) > tail_bytes:
            fp.seek(-tail_bytes - 1, 2)
            if fp.read(1) != b"\n":
                fp.readline()
        else:
            fp.seek(0)
        for index, line in enumerate(fp):
            try:
                record = json.loads(line)
            except ValueError:
                continue
            if isinstance(record, dict):
                yield index, record


# 历史折叠的账本回读上限（7 天账单体积有限，尾部 512KB 足够覆盖近期执行）
_HISTORY_LEDGER_TAIL_BYTES = 524_288


def _ledger_number(value: Any) -> float:
    """旧行或损坏字段无可用数值时保留记录，时间未知按零展示。"""
    try:
        number = float(value)
    except (ValueError, TypeError, OverflowError):
        return 0.0
    return number if math.isfinite(number) else 0.0


def recent_history(limit: int = 20) -> List[Dict[str, Any]]:
    """近期委托执行历史（面板「最近执行」数据源）。

    账本 started/closed 按 id 配对折叠为执行条目；只收录已闭合
    （含 lost）的委托——运行中的由 running_snapshot_all 实时呈现。
    按结束时间倒序，limit 截断。
    """
    started: Dict[str, Dict[str, Any]] = {}
    closed: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
    path = ledger_path()
    try:
        with file_write_lock(path):
            for _, record in _ledger_records(path, _HISTORY_LEDGER_TAIL_BYTES):
                did = str(record.get("id") or "")
                if not did:
                    continue
                if record.get("event") == LEDGER_STARTED:
                    started[did] = record
                elif record.get("event") == LEDGER_CLOSED:
                    closed[did] = (started.pop(did, {}), record)
    except OSError:
        return []
    items: List[Dict[str, Any]] = []
    for did, (start_rec, close_rec) in closed.items():
        started_ts = _ledger_number(start_rec.get("ts"))
        finished_ts = _ledger_number(close_rec.get("ts"))
        items.append({
            "delegation_id": did,
            "goal": str(start_rec.get("goal", "")),
            "scope": str(start_rec.get("scope", "")),
            "agent": str(start_rec.get("agent", "")),
            "model": str(start_rec.get("model", "")),
            "adapter_key": str(start_rec.get("adapter_key", "")),
            "status": str(close_rec.get("status", "")),
            "started_at": started_ts,
            "finished_at": finished_ts,
            "duration_seconds": max(0, int(finished_ts - started_ts)) if started_ts else 0,
            "parent_id": str(start_rec.get("parent_id", "")),
            "depth": int(_ledger_number(start_rec.get("depth"))),
            "resumed_from": str(start_rec.get("resumed_from", "")),
            "summary": str(close_rec.get("summary", "")),
            "usage": close_rec.get("usage", {}),
        })
    items.sort(key=lambda item: float(item["finished_at"]), reverse=True)
    return items[: max(1, limit)]


def unclosed_delegations(is_live: Callable[[str], bool] | None = None) -> List[Dict[str, Any]]:
    """扫描账本中未闭合的委托（started 无对应 closed；同一 id 以最后事件为准）。

    扫描与标记 lost 共用追加锁，仅保存仍未闭合的启动记录。
    """
    path = ledger_path()
    pending: dict[str, dict[str, Any]] = {}
    with file_write_lock(path):
        try:
            for _, record in _ledger_records(path):
                did = str(record.get("id") or "")
                if did and record.get("event") == LEDGER_STARTED:
                    pending[did] = record
                elif record.get("event") == LEDGER_CLOSED:
                    pending.pop(did, None)
        except OSError:
            return []
        lost = []
        for did, record in pending.items():
            if is_live is not None and is_live(did):
                continue
            if append_ledger(LEDGER_CLOSED, did, status=STATUS_LOST):
                lost.append(record)
    return lost


def _compact_ledger(path: Path, cutoff: float) -> None:
    """按闭合时间裁剪完整尝试，跨期起始行和未结行按原顺序保留。调用方持有追加锁。"""
    pending: dict[str, int] = {}
    keep: set[int] = set()
    expired = False
    for index, record in _ledger_records(path):
        did = str(record.get("id") or "")
        event = record.get("event")
        if did and event == LEDGER_STARTED:
            pending[did] = index
            continue
        if event != LEDGER_CLOSED:
            keep.add(index)
            continue
        start = pending.pop(did, None)
        if 0 < _ledger_number(record.get("ts")) < cutoff:
            expired = True
            continue
        if start is not None:
            keep.add(start)
        keep.add(index)
    if not expired:
        return
    keep.update(pending.values())
    with atomic_binary_writer(path) as out, path.open("rb") as source:
        for index, line in enumerate(source):
            if index in keep:
                out.write(line)


def _purge_expired() -> None:
    """保留期内的已结委托和所有未结委托共用同一账本。调用方持有追加锁。"""
    days = _retention_days()
    root = delegation_dir()
    if days <= 0 or not root.is_dir():
        return
    now = time.monotonic()
    if now < _purge_after.get(root, 0):
        return
    if len(_purge_after) >= 16:
        _purge_after.pop(next(iter(_purge_after)))
    _purge_after[root] = now + 600
    cutoff = time.time() - days * 86400
    try:
        _compact_ledger(ledger_path(), cutoff)
    except OSError as exc:
        log(f"委托账本压缩失败，保留原文件: {exc}", "DEBUG", tag="委托")
    try:
        for entry in root.iterdir():
            if entry.suffix not in (".log", ".json") or entry.name == "ledger.jsonl":
                continue
            try:
                if entry.stat().st_mtime < cutoff:
                    entry.unlink()
            except OSError:
                continue
    except OSError:
        pass
