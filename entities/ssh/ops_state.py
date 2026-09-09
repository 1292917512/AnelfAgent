"""SSH 操作态势 — 按会话追踪远程操作并注入 PFC volatile 层。

与 entities/filesystem/ops_context.py 同构：记录于工具执行时（tools.py
各工具经 track_ssh_op 装饰器回报），渲染于 provide()（纯内存读，零远程
I/O）。远程目录说明文档在操作成功后由后台采集任务抓取
（manager.run_capture 旁路通道，失败静默），经短缓存供渲染使用；
会话停止操作超过 ssh_ops_ttl_seconds 后渲染返回 None，注入自动消失。

远程工作目录由 manager 的 POSIX pwd 捕获维护（cd 对后续命令生效），
仅跟踪成功捕获的目录；非 POSIX 远端可关闭 ssh_work_dir_tracking。

Model Experience:
- 模型看到什么：本会话操作过的远程主机态势（连接状态 / 远程目录 /
  目录说明文档摘要 / 最近操作流水）；未操作的主机不出现，其他会话零感知
- token 影响：仅操作活跃窗口内存在（默认 10 分钟无操作即消失），稳态
  为零；上限受 provider max_tokens 与文档配额（单篇字符）约束
- 缓存影响：注入块位于 volatile 尾部动态区（provider 层），不触碰
  stable/summary/conversation 前缀层
"""

from __future__ import annotations

import asyncio
import os
import re
import shlex
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Dict, List, Optional, Set, Tuple

from core.config import get_config, get_config_bool, get_config_int
from core.log import log
from entities._sdk import ToolOp, track_ops

from .manager import STATUS_CONNECTED, get_ssh_manager
from .store import get_ssh_store

# 会话追踪表的容量上限（LRU 淘汰，对齐 ContextProviderRegistry 口径）
_MAX_TRACKED_SCOPES = 200
# 每个会话最多追踪的连接数（LRU 淘汰）
_MAX_CONNS_PER_SCOPE = 10
# 文档名白名单字符（防配置被引导读取远程任意路径）
_DOC_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")
# 文档抓取输出标记
_DOC_BEGIN = "__ANELF_DOC_BEGIN__"
_DOC_END = "__ANELF_DOC_END__"
# 操作后文档缓存的最小复用窗口：窗口期内的连续操作不重复抓取
_POST_OP_REFRESH_SECONDS = 30.0
# 文档抓取命令的远程超时（秒）
_DOC_FETCH_TIMEOUT = 20.0
_DEFAULT_DOC_NAMES = "AGENTS.md,README.md"


@dataclass
class SshOp:
    """一次远程操作事实。"""

    ts: float
    kind: str
    target: str
    ok: bool
    note: str
    duration_ms: int


class _ConnSession:
    """单个会话对单个连接的操作态势（ops 旧→新有序）。"""

    __slots__ = ("last_active", "ops")

    def __init__(self) -> None:
        self.last_active = time.time()
        self.ops: List[SshOp] = []


@dataclass
class _DirDocs:
    """远程目录说明文档缓存。"""

    fetched_at: float
    docs: Dict[str, str]


_scoped: "OrderedDict[str, OrderedDict[str, _ConnSession]]" = OrderedDict()
# 文档缓存：连接名 -> 远程目录 -> 缓存（全局共享，与连接池同口径）
_doc_cache: Dict[str, Dict[str, _DirDocs]] = {}
_doc_inflight: Set[Tuple[str, str]] = set()
_lock = threading.Lock()


def sanitize_doc_names(raw: str) -> List[str]:
    """解析逗号分隔的文档名配置：仅保留白名单字符的纯文件名。"""
    names: List[str] = []
    for part in raw.split(","):
        name = part.strip()
        if name and _DOC_NAME_RE.match(name) and name not in names:
            names.append(name)
    return names[:5]


def build_doc_fetch_command(directory: str, names: List[str], max_chars: int) -> str:
    """组装远程文档抓取命令：目录内逐文件存在性检查后输出带标记内容。"""
    checks = []
    for name in names:
        quoted = shlex.quote(name)
        checks.append(
            f"if [ -f {quoted} ]; then "
            f'printf "{_DOC_BEGIN}%s\\n" {quoted}; '
            f"head -c {max_chars} {quoted}; "
            f'printf "\\n{_DOC_END}\\n"; '
            "fi"
        )
    return f"cd {shlex.quote(directory)} && " + "; ".join(checks)


def parse_doc_fetch_output(output: str) -> Dict[str, str]:
    """解析带标记的文档抓取输出为 {文件名: 内容}。"""
    docs: Dict[str, str] = {}
    pattern = re.compile(
        re.escape(_DOC_BEGIN) + r"(.*?)\n(.*?)\n" + re.escape(_DOC_END),
        re.DOTALL,
    )
    for match in pattern.finditer(output):
        name = match.group(1).strip()
        content = match.group(2).strip("\n")
        if name and content:
            docs[name] = content
    return docs


def _resolve_conn(op: ToolOp) -> str:
    """从工具参数解析目标连接名（空则取默认连接）；无法解析返回空串。"""
    name = str(op.arguments.get("name") or "").strip()
    if not name:
        try:
            name = get_ssh_store().get_default_name()
        except Exception as exc:
            log(f"SSH 态势解析默认连接失败（已忽略）: {exc}", "DEBUG", tag="OpsTrack")
            name = ""
    return name


def record_tool_op(op: ToolOp) -> None:
    """track_ops 回报入口：按 (scope, 连接) 记录操作，成功后触发文档刷新。"""
    if not op.scope or op.scope == "_global":
        return
    conn = _resolve_conn(op)
    if not conn:
        return
    max_entries = max(1, get_config_int("ssh_ops_max_entries", 8))
    with _lock:
        conns = _scoped.get(op.scope)
        if conns is None:
            conns = OrderedDict()
            _scoped[op.scope] = conns
        _scoped.move_to_end(op.scope)
        while len(_scoped) > _MAX_TRACKED_SCOPES:
            _scoped.popitem(last=False)
        session = conns.get(conn)
        if session is None:
            session = _ConnSession()
            conns[conn] = session
        conns.move_to_end(conn)
        while len(conns) > _MAX_CONNS_PER_SCOPE:
            conns.popitem(last=False)
        session.last_active = time.time()
        session.ops.append(SshOp(
            ts=session.last_active, kind=op.tool, target=op.target,
            ok=op.ok, note=op.note, duration_ms=op.duration_ms,
        ))
        del session.ops[:-max_entries]
    if op.ok:
        _maybe_refresh_after_op(op, conn)


def _cache_seconds() -> int:
    return max(30, get_config_int("ssh_remote_doc_cache_seconds", 300))


def _maybe_refresh_after_op(op: ToolOp, conn: str) -> None:
    """操作成功后按需触发远程目录文档的后台刷新（最小复用窗口防震荡）。"""
    if not get_config_bool("ssh_remote_docs_enabled", True):
        return
    directory = ""
    if op.tool == "ssh_exec":
        snapshot = get_ssh_manager().get_snapshot(conn)
        directory = (snapshot or {}).get("work_dir") or ""
    elif op.tool == "ssh_upload":
        directory = os.path.dirname(str(op.arguments.get("remote_path") or ""))
    if not directory:
        return
    now = time.time()
    with _lock:
        cached = _doc_cache.get(conn, {}).get(directory)
        age = now - cached.fetched_at if cached else float("inf")
        if age < min(_POST_OP_REFRESH_SECONDS, _cache_seconds()):
            return
        key = (conn, directory)
        if key in _doc_inflight:
            return
        _doc_inflight.add(key)
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        with _lock:
            _doc_inflight.discard(key)
        return
    loop.create_task(_refresh_docs(conn, directory))


async def _refresh_docs(conn: str, directory: str) -> None:
    """后台抓取远程目录的说明文档并写入缓存（任何失败静默，仅 DEBUG 日志）。"""
    key = (conn, directory)
    try:
        names = sanitize_doc_names(str(get_config("ssh_remote_doc_names", _DEFAULT_DOC_NAMES)))
        if not names:
            return
        max_chars = max(200, get_config_int("ssh_remote_doc_max_chars", 3000))
        command = build_doc_fetch_command(directory, names, max_chars)
        output = await get_ssh_manager().run_capture(conn, command, timeout=_DOC_FETCH_TIMEOUT)
        if output is None:
            return
        docs = parse_doc_fetch_output(output)
        with _lock:
            _doc_cache.setdefault(conn, {})[directory] = _DirDocs(time.time(), docs)
    except Exception as exc:
        log(f"SSH 远程文档抓取失败（已忽略）: {conn}:{directory} - {exc}",
            "DEBUG", tag="OpsTrack")
    finally:
        with _lock:
            _doc_inflight.discard(key)


def render_scope(scope: str) -> Optional[str]:
    """渲染指定会话的 SSH 操作态势；无近期操作返回 None（并清理会话）。"""
    if not scope:
        return None
    ttl = max(30, get_config_int("ssh_ops_ttl_seconds", 600))
    now = time.time()
    with _lock:
        conns = _scoped.get(scope)
        if not conns:
            return None
        fresh = [(name, s, list(s.ops))
                 for name, s in conns.items() if now - s.last_active <= ttl]
        if not fresh:
            del _scoped[scope]
            return None
    manager = get_ssh_manager()
    docs_enabled = get_config_bool("ssh_remote_docs_enabled", True)
    max_entries = max(1, get_config_int("ssh_ops_max_entries", 8))
    lines = ["[SSH 操作态势] 本会话近期操作的远程主机（实时快照，停止操作后自动消失）"]
    for name, _session, ops in fresh:
        snapshot = manager.get_snapshot(name) or {}
        title = name
        host, user = snapshot.get("host", ""), snapshot.get("username", "")
        if host:
            title += f" ({user}@{host})"
        status = snapshot.get("status", "")
        if status == STATUS_CONNECTED:
            title += "（在线）"
        elif status:
            title += f"（{status}）"
        lines.append(f"── {title} ──")
        work_dir = snapshot.get("work_dir") or ""
        if work_dir:
            lines.append(f"远程目录: {work_dir}")
            if docs_enabled:
                with _lock:
                    dir_docs = _doc_cache.get(name, {}).get(work_dir)
                if dir_docs:
                    for doc_name, content in dir_docs.docs.items():
                        lines.append(f"## {work_dir}/{doc_name}")
                        lines.append(content)
        lines.append("最近操作（新→旧）:")
        for op in reversed(ops[-max_entries:]):
            mark = "✓" if op.ok else "✗"
            note = f"（{op.note}）" if op.note else ""
            stamp = time.strftime("%H:%M:%S", time.localtime(op.ts))
            target = f" {op.target}" if op.target else ""
            lines.append(f"- {stamp} {op.kind}{target} {mark}{note}")
    return "\n".join(lines)


def track_ssh_op(*target_params: str):
    """SSH 工具操作回报装饰器（track_ops 绑定本模块追踪器）。"""
    return track_ops(record_tool_op, *target_params)
