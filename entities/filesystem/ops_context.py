"""文件操作态势 — 按会话追踪本机文件操作并注入 PFC volatile 层。

记录发生在工具执行时（tools.py 各工具经 track_fs_op 装饰器回报，参数齐全
零解析），渲染发生在 provide()（内存读 + 目录说明文档的 mtime 校验读取，
异常 fail-open 不注入）。会话停止操作超过 os_context_ttl_seconds 后渲染
返回 None，注入自动消失（注册表收集滞后一轮，最晚两轮内消失）。

Model Experience:
- 模型看到什么：本会话的文件操作态势（当前 Shell 目录 / 操作目录下的约定
  说明文档摘要 / 最近操作流水），仅注入正在操作的会话，其他会话零感知
- token 影响：仅操作活跃窗口内存在（默认 10 分钟无操作即消失），稳态为零；
  上限受 provider max_tokens 与文档配额（个数 × 单篇字符）双重约束
- 缓存影响：注入块位于 volatile 尾部动态区（provider 层），不触碰
  stable/summary/conversation 前缀层
"""

from __future__ import annotations

import os
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import List, Optional, Tuple

from core.config import get_config, get_config_bool, get_config_int, register_configs_safe
from core.log import log
from entities._sdk import ToolOp, context_provider, track_ops

from . import shell_state

# 每个会话保留的活跃目录上限（旧→新有序，超出淘汰最旧）
_MAX_ACTIVE_DIRS = 5
# 会话追踪表的容量上限（LRU 淘汰，对齐 ContextProviderRegistry 口径）
_MAX_TRACKED_SCOPES = 200
# 说明文档内容缓存的容量上限（按路径 LRU 淘汰）
_MAX_DOC_CACHE = 50
# 每个目录最多注入一份说明文档（按配置顺序取首个命中）
_DEFAULT_DOC_NAMES = "AGENTS.md,README.md"

_OPS_CONFIGS = {
    "entity/os": {
        "workspace_root": {
            "description": (
                "工作区根目录：文件/Shell/Python 工具所有相对路径的统一锚点。"
                "相对路径以项目根为基准解析；支持绝对路径把工作区放到项目外。"
                "不可设为项目根或其上级目录（会把项目本体暴露为可写，自动回退默认）"
            ),
            "default": "workspace",
        },
        "sandbox_enabled": {
            "description": "沙箱：开启时文件与 Shell 写操作限制在工作区内，漂出自动拦截",
            "default": True,
        },
        "os_context_inject": {
            "description": "是否向 AI 上下文注入文件操作态势（当前目录/目录说明文档/最近操作）",
            "default": True,
        },
        "os_context_ttl_seconds": {
            "description": "停止文件操作多久后取消态势注入",
            "default": 600,
            "advanced": True,
            "unit": "秒",
        },
        "os_context_max_ops": {
            "description": "最近操作流水保留并注入的最大条数",
            "default": 8,
            "advanced": True,
        },
        "os_context_docs_enabled": {
            "description": "是否注入操作目录下的说明文档（如 AGENTS.md）",
            "default": True,
        },
        "os_context_doc_names": {
            "description": "注入的目录说明文档文件名（逗号分隔，每个目录取首个命中）",
            "default": _DEFAULT_DOC_NAMES,
            "advanced": True,
        },
        "os_context_doc_max_chars": {
            "description": "单份说明文档注入的最大字符数（超出截断）",
            "default": 3000,
            "advanced": True,
            "unit": "字符",
        },
        "os_context_doc_max_files": {
            "description": "每次最多注入的说明文档份数（当前目录优先，其次最近活跃目录）",
            "default": 2,
            "advanced": True,
        },
    },
}

register_configs_safe(_OPS_CONFIGS)


@dataclass
class FsOp:
    """一次文件操作事实。"""

    ts: float
    tool: str
    target: str
    ok: bool
    note: str
    duration_ms: int


class _OpsSession:
    """单个会话的文件操作态势（ops/dirs 均旧→新有序）。"""

    __slots__ = ("last_active", "ops", "dirs")

    def __init__(self) -> None:
        self.last_active = time.time()
        self.ops: List[FsOp] = []
        self.dirs: "OrderedDict[str, None]" = OrderedDict()


_sessions: "OrderedDict[str, _OpsSession]" = OrderedDict()
# 说明文档缓存：路径 -> (mtime, 内容)，mtime 一致即命中（写操作更新 mtime 自动失效）
_doc_cache: "OrderedDict[str, Tuple[float, str]]" = OrderedDict()
_lock = threading.Lock()


def _workspace_root() -> str:
    """工作区根目录（与 tools.safe_path 同源热读取）。"""
    return os.path.abspath(str(get_config("workspace_root", "workspace")))


def _target_dir(raw: str, workspace: str) -> Optional[str]:
    """把路径类目标解析为其所属目录；非路径目标（shell 命令等）返回 None。"""
    if not raw or any(c in raw for c in " \t\n`*$&|;<>(){}'\""):
        return None
    path = os.path.expanduser(raw)
    if not os.path.isabs(path):
        path = os.path.join(workspace, path)
    path = os.path.normpath(path)
    if os.path.isdir(path):
        return path
    parent = os.path.dirname(path)
    return parent if os.path.isdir(parent) else None


def record_tool_op(op: ToolOp) -> None:
    """track_ops 回报入口：更新会话态势（活跃目录从路径类目标提取）。"""
    if not op.scope or op.scope == "_global":
        return
    max_ops = max(1, get_config_int("os_context_max_ops", 8))
    with _lock:
        session = _sessions.get(op.scope)
        if session is None:
            session = _OpsSession()
            _sessions[op.scope] = session
        _sessions.move_to_end(op.scope)
        while len(_sessions) > _MAX_TRACKED_SCOPES:
            _sessions.popitem(last=False)
        session.last_active = time.time()
        session.ops.append(FsOp(
            ts=session.last_active, tool=op.tool, target=op.target,
            ok=op.ok, note=op.note, duration_ms=op.duration_ms,
        ))
        del session.ops[:-max_ops]
    # 目录提取放锁外（os.path.isdir 是文件系统 I/O）
    workspace = _workspace_root()
    for raw in op.targets:
        directory = _target_dir(raw, workspace)
        if directory is None:
            continue
        with _lock:
            session.dirs[directory] = None
            session.dirs.move_to_end(directory)
            while len(session.dirs) > _MAX_ACTIVE_DIRS:
                session.dirs.popitem(last=False)


def _doc_names() -> List[str]:
    """解析文档名配置：仅保留纯文件名（防配置被引导读取任意路径）。"""
    raw = str(get_config("os_context_doc_names", _DEFAULT_DOC_NAMES))
    names: List[str] = []
    for part in raw.split(","):
        name = part.strip()
        if name and os.path.basename(name) == name and name not in names:
            names.append(name)
    return names[:5]


def _read_doc(path: str, max_chars: int) -> Optional[str]:
    """按 mtime 缓存读取说明文档（超限截断）；不存在/读取失败返回 None。"""
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return None
    with _lock:
        cached = _doc_cache.get(path)
    if cached is not None and cached[0] == mtime:
        return cached[1]
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read(max_chars + 1)
    except OSError as exc:
        log(f"目录说明文档读取失败（已忽略）: {path} - {exc}", "DEBUG", tag="OpsTrack")
        return None
    if len(content) > max_chars:
        content = content[:max_chars].rstrip() + "\n…（文档过长已截断）"
    with _lock:
        _doc_cache[path] = (mtime, content)
        _doc_cache.move_to_end(path)
        while len(_doc_cache) > _MAX_DOC_CACHE:
            _doc_cache.popitem(last=False)
    return content


def _collect_docs(session: _OpsSession, cwd: str) -> List[Tuple[str, str]]:
    """收集应注入的目录说明文档：当前 Shell 目录优先，其次最近活跃目录（新→旧）。"""
    if not get_config_bool("os_context_docs_enabled", True):
        return []
    names = _doc_names()
    if not names:
        return []
    max_chars = max(200, get_config_int("os_context_doc_max_chars", 3000))
    max_files = max(1, get_config_int("os_context_doc_max_files", 2))
    with _lock:
        dirs = [cwd] + list(reversed(session.dirs))
    docs: List[Tuple[str, str]] = []
    seen = set()
    for directory in dirs:
        if directory in seen:
            continue
        seen.add(directory)
        for name in names:
            content = _read_doc(os.path.join(directory, name), max_chars)
            if content is not None:
                docs.append((os.path.join(directory, name), content))
                break
        if len(docs) >= max_files:
            break
    return docs


def render_session(scope: str) -> Optional[str]:
    """渲染指定会话的文件操作态势；无态势或超过 TTL 返回 None（并清理会话）。"""
    if not scope:
        return None
    ttl = max(30, get_config_int("os_context_ttl_seconds", 600))
    with _lock:
        session = _sessions.get(scope)
        if session is None:
            return None
        if time.time() - session.last_active > ttl:
            del _sessions[scope]
            return None
        ops = list(session.ops)
    workspace = _workspace_root()
    cwd = shell_state.get_cwd(workspace, scope, sandbox=bool(get_config("sandbox_enabled", True)))
    lines = [
        "[文件操作态势] 本会话正在操作本机文件（实时快照，停止操作后自动消失）",
        f"当前 Shell 目录: {cwd}",
    ]
    docs = _collect_docs(session, cwd)
    if docs:
        lines.append("目录说明文档:")
        for path, content in docs:
            lines.append(f"── {path} ──")
            lines.append(content)
    if ops:
        max_ops = max(1, get_config_int("os_context_max_ops", 8))
        lines.append("最近操作（新→旧）:")
        for op in reversed(ops[-max_ops:]):
            mark = "✓" if op.ok else "✗"
            note = f"（{op.note}）" if op.note else ""
            stamp = time.strftime("%H:%M:%S", time.localtime(op.ts))
            target = f" {op.target}" if op.target else ""
            lines.append(f"- {stamp} {op.tool}{target} {mark}{note}")
    return "\n".join(lines)


def track_fs_op(*target_params: str):
    """文件工具操作回报装饰器（track_ops 绑定本模块追踪器）。"""
    return track_ops(record_tool_op, *target_params)


@context_provider(
    name="fs_ops", priority=32, max_tokens=2000,
    group="os", inject_key="os_context_inject",
)
async def fs_ops_provider(scope: str) -> Optional[str]:
    """文件操作态势（当前目录 / 目录说明文档 / 最近操作）。"""
    return render_session(scope)
