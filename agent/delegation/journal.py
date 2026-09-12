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
  与 reply_checkpoints 同范式（at-least-once：注入成功才闭合）。

容量纪律：retention 天数滚动清理（新委托启动时顺带执行，mtime 扫描，
目录不遍历进热路径）；写入全部 fail-open——日志失败绝不影响委托执行。
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.config import get_config_int
from core.file_utils import atomic_write_text
from core.log import log
from core.path import ConfigPaths

# transcript 消息链的序列化上限（超出则不落 messages，标记不可续跑）
TRANSCRIPT_MAX_BYTES = 262_144

# 账本事件类型（读取方按词消费）
LEDGER_STARTED = "started"
LEDGER_CLOSED = "closed"


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

def append_ledger(event: str, delegation_id: str, **fields: Any) -> None:
    """账本追加一行事件（fail-open；顺带执行 retention 清理）。"""
    try:
        path = ledger_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "event": event,
            "id": delegation_id,
            "ts": round(time.time(), 3),
            **fields,
        }
        with path.open("a", encoding="utf-8") as fp:
            fp.write(json.dumps(record, ensure_ascii=False) + "\n")
        _purge_expired()
    except OSError as exc:
        log(f"委托账本写入失败（已忽略）: {delegation_id}: {exc}", "DEBUG", tag="委托")


def unclosed_delegations() -> List[Dict[str, Any]]:
    """扫描账本中未闭合的委托（started 无对应 closed；同一 id 以最后事件为准）。

    返回的条目随即在账本闭合（status=lost）——at-least-once：注入失败由
    调用方决定是否重试，此处先闭合防重启风暴；注入内容只含事实（目标/
    会话/启动时间），丢失无 irreversible 后果。
    """
    path = ledger_path()
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    latest: Dict[str, Dict[str, Any]] = {}
    for line in lines:
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(record, dict):
            continue
        did = str(record.get("id", ""))
        if did:
            latest[did] = record
    unclosed: List[Dict[str, Any]] = []
    for did, record in latest.items():
        if record.get("event") != LEDGER_STARTED:
            continue
        unclosed.append(record)
        append_ledger(LEDGER_CLOSED, did, status="lost")
    return unclosed


def _purge_expired() -> None:
    """retention 滚动清理：删除超期的进度流与 transcript（幂等，失败静默）。"""
    days = _retention_days()
    root = delegation_dir()
    if days <= 0 or not root.is_dir():
        return
    cutoff = time.time() - days * 86400
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
