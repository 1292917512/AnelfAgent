"""操作系统实体 — 文件操作 + Shell/Python 执行。

文件路径操作受沙箱保护，默认限制在 workspace/ 目录下。
沙箱通过 app_config.json 中的 workspace_root 和 sandbox_enabled 配置。

edit_file/read_file/write_file 的编辑安全语义移植自 Claude Code
（read-before-write、mtime 过期检查、弯引号容忍匹配、行尾往返），
详见 docs/refactor/01-claudecode-tools.md。

Model Experience（run_shell_command 失败归因 notes）:
- 模型看到什么：命令失败且命中归因模式时，结果 notes 附带事实陈述——
  记忆索引键的真实位置，或解释器为 uv venv 且不含 pip（无操作建议）
- token 影响：仅失败时 +50~150 字符，属 tool_chain 尾部动态区
- 缓存影响：不触碰任何前缀层（notes 在工具结果内，volatile 语义）

Model Experience（写操作成功结果 location 标注）:
- 模型看到什么：write/edit/append/copy/move/delete/mkdir 的成功结果多一个
  location 键（inside_workspace / outside_workspace），明确本次操作的实际
  落点是否在工作区内（沙箱关闭或审批放行的区外写也能成功，需让 AI 知情）
- token 影响：每次写操作约 +5 token，属 tool_chain 尾部动态区
- 缓存影响：不触碰任何前缀层（工具结果在尾部动态区）
"""

from __future__ import annotations

import json
import os
import re
import shlex
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

from entities._sdk import (
    ErrorCause,
    coerce_bool_arg,
    entity,
    error_from_exception,
    tool,
    tool_error,
)
from entities.filesystem import edit_utils, file_state

# 顶部导入：shell_background 的配置注册（entity/os 组）随模块加载生效，
# 配置中心启动即可见；惰性导入会让配置项迟到首次后台执行
from entities.filesystem.shell_background import launch_background

entity("os", "操作系统 - 文件读写、目录管理、Shell 命令、Python 执行")

# ------------------------------------------------------------------
# 沙箱路径解析
# ------------------------------------------------------------------

_WORKSPACE = "workspace"
_SANDBOX = True


def _load_config() -> None:
    global _WORKSPACE, _SANDBOX
    try:
        from core.config import ConfigManager
        _WORKSPACE = ConfigManager.get("workspace_root", "workspace")
        _SANDBOX = ConfigManager.get("sandbox_enabled", True)
    except Exception as e:
        from core.log import log
        log(f"文件系统沙箱配置加载失败: {e}", "DEBUG")


def safe_path(path: str) -> str:
    """解析路径并执行沙箱检查。相对路径基于 workspace_root 解析。

    解析逻辑与权限层共用 entities/filesystem/paths.py（防权限绕过：
    规则匹配的规范化结果与这里完全一致）。
    """
    from entities.filesystem import paths as _paths
    _load_config()
    ws_abs = os.path.abspath(_WORKSPACE)
    os.makedirs(ws_abs, exist_ok=True)
    resolved = _paths.resolve_workspace_path(path, ws_abs)
    if _SANDBOX and not _paths.check_sandbox(resolved, ws_abs):
        raise ValueError(f"沙箱限制: {path} 不在工作目录 ({_WORKSPACE}) 内")
    return resolved


def _location_of(fp: str) -> str:
    """标注解析后路径的工作区归属（inside_workspace / outside_workspace）。

    成功结果中告知 AI 本次操作的实际位置：沙箱关闭或经审批放行的区外
    操作本可成功，AI 需要明确知道"这次写到了工作区外"。判定与沙箱同源
    （paths.check_sandbox，realpath 展开符号链接）。
    """
    from entities.filesystem import paths as _paths
    return "inside_workspace" if _paths.check_sandbox(fp) else "outside_workspace"


# ------------------------------------------------------------------
# 工具长 prompt（对齐 Claude Code prompt.ts，经 description 参数完整进入 schema）
# ------------------------------------------------------------------

_READ_FILE_PROMPT = """读取文本文件内容，输出带行号（格式: 行号→内容）。

使用规则:
- 行号前缀（如 "12→"）不是文件内容，edit_file 的 old_string/new_string 绝不可包含它。
- 大文件必须用 offset/limit 分段读取（上限 2000 行 / 256KB / 25000 token）。
- 同一文件同一范围重复读取会返回"未变化"存根，直接参考此前的读取结果。
- 读取图片/音频/视频等二进制文件请使用对应的媒体工具（recognize_image 等）。"""

_WRITE_FILE_PROMPT = """写入文件（整体覆盖）。目录不存在时自动创建。

使用规则:
- 覆盖已有文件前必须先用 read_file 读取过该文件，否则本工具会报错。
- 修改已有文件请优先使用 edit_file — 它只发送差异部分，更省 token 且不易出错。
- 除非明确要求，不要新建文档类文件（*.md/README）。"""

_EDIT_FILE_PROMPT = """在文件中执行精确的字符串替换 — 修改已有文件的首选方式。

使用规则:
- 修改文件前必须先用 read_file 读取过该文件，否则本工具会报错。
- old_string 必须与文件内容精确匹配：保持 read_file 输出中行号前缀（→）之后的原始缩进（tab/空格）。
- old_string 不唯一时会失败：提供包含更多上下文的更大字符串以唯一定位，
  或设置 replace_all=True 替换所有出现处（适合重命名变量/函数）。
- 优先编辑已有文件，除非明确要求否则不要新建文件。
- 修改成功后无需再用 read_file 验证——失败会明确报错，重复读取浪费 token。
- 除非用户要求，不要在代码中添加 emoji。"""

_SHELL_PROMPT = """在系统 shell 中执行命令并返回输出。

工作目录:
- 初始目录即工作区根目录（绝对路径见系统提示 [运行环境]），直接用相对路径，不存在嵌套的 workspace/workspace。
- 优先在工作区内操作；访问其他位置用绝对路径并先 ls 确认目标存在，禁止凭记忆猜路径。

执行环境:
- 每条命令独立进程（环境变量/alias 不保留），但 cd 对后续命令生效；沙箱下漂出 workspace 自动重置。
- 输出超 30000 字符自动落盘，返回预览和文件路径（用 read_file 查看）。
- 超时由 timeout 参数指定：前台默认 120 秒（到时返回失败结果）；run_in_background=True 后台执行
  不受强制超时——超过预期时长（默认 1800 秒）系统会提醒你并附最新进度，是否终止由你决定
  （terminate_background_task）。

工具偏好（不要用 shell 做这些事）: 搜索用 search_files，读取用 read_file，编辑用 edit_file，写入用 write_file。

注意事项:
- 操作数据库（sqlite3 等）前先查表结构（.schema / PRAGMA table_info），禁止臆测表名和列名。
- 路径含空格务必加引号。"""


# ------------------------------------------------------------------
# 工具
# ------------------------------------------------------------------

# 读取上限（对齐 Claude Code FileReadTool/limits.ts）
_READ_MAX_LINES = 2000
_READ_MAX_BYTES = 256 * 1024
_READ_MAX_TOKENS = 25000  # 按 ~4 字符/token 估算


def _read_text_with_metadata(fp: str, encoding: str = "utf-8") -> Tuple[str, str, str]:
    """读取文件文本并做行尾归一化。

    Returns:
        (内容（CRLF 已归一为 LF）, 实际编码, 原行尾风格 "CRLF"|"LF")
    """
    with open(fp, "rb") as f:
        raw = f.read()
    enc = encoding
    if raw.startswith(b"\xff\xfe") or raw.startswith(b"\xfe\xff"):
        enc = "utf-16"
    content = raw.decode(enc, errors="replace")
    eol = "CRLF" if "\r\n" in content else "LF"
    return content.replace("\r\n", "\n"), enc, eol


def _write_text_with_metadata(fp: str, content: str, encoding: str = "utf-8",
                              eol: str = "LF") -> None:
    """按原编码与行尾风格写回文件（CRLF 文件写回 CRLF），经原子写落盘。"""
    if eol == "CRLF":
        content = content.replace("\r\n", "\n").replace("\n", "\r\n")
    _atomic_write_bytes(fp, content.encode(encoding))


def _atomic_write_bytes(fp: str, data: bytes) -> None:
    """原子写：先写同目录临时文件，fsync 后 os.replace 到目标。

    长驻进程下避免"写到一半崩溃/断电留下半截文件"（codex apply-patch 也未做
    原子写，此处为长驻场景的加强）。临时文件与目标同目录（保证同文件系统，
    rename 才是原子的）；异常路径负责清理临时文件。
    """
    import tempfile
    directory = os.path.dirname(fp) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=f".{os.path.basename(fp)}.",
                               suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, fp)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def _add_line_numbers(content: str, start_line: int = 1) -> str:
    """添加行号前缀（格式: 行号→内容）。前缀不是文件内容，编辑时不得包含。"""
    lines = content.split("\n")
    width = len(str(start_line + len(lines) - 1))
    return "\n".join(f"{i:>{width}}→{line}" for i, line in enumerate(lines, start_line))


@tool(name="read_file", group="os", tags=["media:file"], concurrency_safe=True, description=_READ_FILE_PROMPT)
def read_file(file_path: str, offset: int = 0, limit: int = 0, encoding: str = "utf-8") -> str:
    """读取文本文件内容，带行号输出（格式: 行号→内容）。大文件请用 offset/limit 分段读取。

    Args:
        file_path: 文件路径（相对于 workspace 或绝对路径）
        offset: 起始行号（从 1 开始），0 表示从头读取
        limit: 最多读取行数，0 表示读取到上限（2000 行）
        encoding: 文件编码，默认 utf-8
    """
    try:
        fp = safe_path(file_path)
        if not os.path.isfile(fp):
            return tool_error(f"文件不存在: {file_path}", cause=ErrorCause.NOT_FOUND,
                              retryable=False, resolved=fp)
        # Binary files: return metadata instead of trying to decode
        bin_exts = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".ico",
                    ".mp3", ".wav", ".ogg", ".flac", ".m4a", ".opus", ".amr",
                    ".mp4", ".avi", ".mkv", ".mov", ".webm",
                    ".zip", ".tar", ".gz", ".7z", ".rar",
                    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
                    ".exe", ".dll", ".so", ".bin", ".dat", ".sqlite3"}
        ext = os.path.splitext(fp)[1].lower()
        if ext == ".ipynb":
            from entities.filesystem.notebook import summarize_notebook
            try:
                return summarize_notebook(fp)
            except Exception as e:
                return error_from_exception(e, action="读取 notebook")
        if ext in bin_exts:
            size = os.path.getsize(fp)
            return json.dumps({
                "type": "binary",
                "path": fp,
                "size": size,
                "ext": ext,
                "hint": "Use recognize_image for images, voice_to_text for audio",
            }, ensure_ascii=False)

        # 内容级二进制嗅探：扩展名表覆盖不到无扩展名/冷门扩展名的二进制文件，
        # 而下方文本读取走 errors="replace" 永不抛解码异常——NUL 采样是唯一防线
        from entities.filesystem.scan import looks_binary
        if looks_binary(fp):
            return json.dumps({
                "type": "binary",
                "path": fp,
                "size": os.path.getsize(fp),
                "ext": ext,
                "hint": "Binary content detected. Use recognize_image for images, "
                        "voice_to_text for audio, or run_shell_command (e.g. `file`) "
                        "to inspect the file type.",
            }, ensure_ascii=False)

        size = os.path.getsize(fp)
        if size > _READ_MAX_BYTES and offset <= 0 and limit <= 0:
            return tool_error(
                f"文件过大（{size} 字节，上限 {_READ_MAX_BYTES}）。"
                "请使用 offset/limit 参数分段读取。",
                cause=ErrorCause.PARAM, retryable=False, path=fp,
            )

        content, _, _ = _read_text_with_metadata(fp, encoding)
        mtime = os.path.getmtime(fp)
        start_line = max(1, offset) if offset > 0 else 1
        max_lines = limit if limit > 0 else _READ_MAX_LINES

        all_lines = content.split("\n")
        total_lines = len(all_lines)
        selected = all_lines[start_line - 1: start_line - 1 + max_lines]
        body = "\n".join(selected)

        # token 估算上限：超出则截断并引导分段
        est_tokens = len(body) // 4
        truncated = False
        if est_tokens > _READ_MAX_TOKENS:
            keep_chars = _READ_MAX_TOKENS * 4
            body = body[:keep_chars]
            truncated = True

        # 完整读取判定：实际覆盖到文件末尾且未被 token 截断才算完整
        # （无 offset 读取 10k 行文件被 2000 行截断 → 仍是部分读取，不授权写入）
        end_line = start_line + len(selected) - 1
        covers_all = start_line == 1 and end_line >= total_lines and not truncated
        is_full_read = covers_all

        # 读重去重：相同范围且文件未变 → 返回存根（对齐 Claude Code Read 去重）
        cached = file_state.get_cache().get(fp)
        if cached is not None and mtime <= cached.mtime:
            same_range = (is_full_read and not cached.is_partial_view) or (
                not is_full_read
                and cached.offset == (offset or None) and cached.limit == (limit or None))
            if same_range:
                return json.dumps({
                    "unchanged": True,
                    "path": fp,
                    "message": "文件自上次读取后未变化，本次会话中此前的读取结果仍然有效，"
                               "请直接参考，不必重复读取。",
                }, ensure_ascii=False)

        numbered = _add_line_numbers(body, start_line)
        tail_notes: List[str] = []
        if end_line < total_lines:
            tail_notes.append(f"（第 {start_line}-{end_line} 行，共 {total_lines} 行；"
                              f"可用 offset={end_line + 1} 继续读取）")
        if truncated:
            tail_notes.append("（内容超出 token 上限已截断，请用更小的 limit 分段读取）")
        if total_lines == 1 and not all_lines[0]:
            numbered = ""
            tail_notes.append("（文件存在但内容为空）")

        file_state.record_read(
            fp, content, mtime,
            offset=None if is_full_read else (offset or None),
            limit=None if is_full_read else (limit or None),
        )
        return numbered + ("\n" + " ".join(tail_notes) if tail_notes else "")
    except UnicodeDecodeError:
        size = os.path.getsize(fp)
        return json.dumps({
            "type": "binary",
            "path": fp,
            "size": size,
            "hint": "Binary file, cannot read as text",
        }, ensure_ascii=False)
    except Exception as e:
        return error_from_exception(e, action="读取文件")


@tool(name="write_file", group="os", description=_WRITE_FILE_PROMPT)
def write_file(file_path: str, content: str) -> str:
    """写入文件（覆盖）。目录不存在时自动创建。修改已有文件前必须先用 read_file 读取；
    对已有文件的局部修改请优先使用 edit_file（只发送差异部分）。

    Args:
        file_path: 文件路径（相对于 workspace）
        content: 要写入的文本内容
    """
    try:
        fp = safe_path(file_path)
        if os.path.exists(fp):
            ok, message = file_state.check_writable(fp)
            if not ok:
                return tool_error(message, cause=ErrorCause.STATE, retryable=False, path=fp)
            # 保留已有文件的行尾风格：edit_file 维护的 CRLF 文件不应被
            # 整体覆盖洗成 LF（git 会显示全文件变更）
            _, encoding, eol = _read_text_with_metadata(fp)
        else:
            encoding, eol = "utf-8", "LF"
        _write_text_with_metadata(fp, content, encoding, eol)
        file_state.record_write(fp, content.replace("\r\n", "\n"), os.path.getmtime(fp))
        return json.dumps({"ok": True, "path": fp, "size": len(content),
                           "location": _location_of(fp)}, ensure_ascii=False)
    except Exception as e:
        return error_from_exception(e, action="写入文件")


# 编辑文件大小上限（对齐 Claude Code MAX_EDIT_FILE_SIZE = 1GiB）
_EDIT_MAX_FILE_BYTES = 1024 * 1024 * 1024


@tool(name="edit_file", group="os", tags=["always"], description=_EDIT_FILE_PROMPT)
def edit_file(file_path: str, old_string: str, new_string: str, replace_all: bool = False) -> str:
    """在文件中执行精确的字符串替换。修改已有文件的首选方式（只发送差异，而非全文）。

    使用规则:
    - 修改文件前必须先用 read_file 读取过该文件，否则本工具会报错。
    - old_string 必须精确匹配文件内容（保持 read_file 输出中行号前缀→之后的原始缩进）。
    - old_string 在文件中不唯一时会失败：请提供包含更多上下文的更大字符串，
      或设置 replace_all=True 替换所有出现处（适合重命名变量）。
    - 优先编辑已有文件，除非明确要求否则不要新建文件。

    Args:
        file_path: 文件路径（相对于 workspace 或绝对路径）
        old_string: 要被替换的原文（必须与 new_string 不同）
        new_string: 替换后的文本
        replace_all: 是否替换所有出现处，默认 False
    """
    # 容忍模型传入字符串形式的布尔值（对齐 Claude Code semanticBoolean）
    replace_all = coerce_bool_arg(replace_all, False)
    try:
        fp = safe_path(file_path)
    except Exception as e:
        return error_from_exception(e, action="解析文件路径")

    def _err(message: str, code: int, cause: Optional[ErrorCause] = None) -> str:
        return tool_error(message, code=code, cause=cause)

    if old_string == new_string:
        return _err("未做任何修改：old_string 与 new_string 完全相同。", 1)

    if file_path.lower().endswith(".ipynb"):
        return _err("notebook 请使用 notebook_edit 按单元格编辑（直接字符串替换易损坏 JSON 结构）。", 5)

    exists = os.path.isfile(fp)
    if not exists:
        if old_string == "":
            # 空 old_string + 文件不存在 = 创建新文件
            try:
                _write_text_with_metadata(fp, new_string, "utf-8", "LF")
                file_state.record_write(fp, new_string, os.path.getmtime(fp))
                return json.dumps({"ok": True, "path": fp,
                                   "message": f"文件创建成功: {fp}",
                                   "location": _location_of(fp)}, ensure_ascii=False)
            except Exception as e:
                return _err(f"创建文件失败: {e}", 11)
        suggestion = _suggest_similar_path(fp)
        return _err(f"文件不存在: {file_path}。{suggestion}", 4)

    if old_string == "":
        return _err("文件已存在，不能用空 old_string 创建。如需整体覆盖请使用 write_file，"
                    "局部修改请提供要替换的原文。", 3)

    try:
        if os.path.getsize(fp) > _EDIT_MAX_FILE_BYTES:
            return _err("文件超过 1GiB，无法编辑。", 10)
        content, encoding, eol = _read_text_with_metadata(fp)
    except Exception as e:
        return _err(f"读取文件失败: {e}", 12)

    ok, message = file_state.check_writable(fp)
    if not ok:
        # 与 write_file 对齐：补结构化 cause（守卫/重试按 cause 路由）
        return _err(message, 6, cause=ErrorCause.STATE)

    # new_string 逐行去尾空格（markdown 的尾空格是硬换行语法，跳过）
    if not file_path.lower().endswith((".md", ".mdx")):
        new_string = edit_utils.strip_trailing_whitespace(new_string)

    actual_old = edit_utils.find_actual_string(content, old_string)
    if actual_old is None:
        preview = old_string[:200] + ("…" if len(old_string) > 200 else "")
        return _err(f"未在文件中找到要替换的字符串。请对照 read_file 的最新输出检查缩进与内容。\n"
                    f"old_string: {preview}", 8)

    occurrences = edit_utils.count_occurrences(content, actual_old)
    if occurrences > 1 and not replace_all:
        return _err(
            f"找到 {occurrences} 处匹配，但 replace_all 为 False。"
            "请提供包含更多上下文的更大字符串以唯一定位，"
            "或设置 replace_all=True 替换所有出现处。", 9)

    final_new = edit_utils.preserve_quote_style(old_string, actual_old, new_string)
    updated = edit_utils.apply_edit_to_file(content, actual_old, final_new, replace_all)
    if updated == content:
        return _err("替换未产生任何变化，应用编辑失败。", 13)

    try:
        _write_text_with_metadata(fp, updated, encoding, eol)
    except Exception as e:
        return _err(f"写入文件失败: {e}", 14)

    file_state.record_write(fp, updated, os.path.getmtime(fp))
    additions, removals = edit_utils.diff_stats(content, updated)
    _emit_file_diff(fp, content, updated, additions, removals)
    replaced = occurrences if replace_all else 1
    result: Dict[str, Any] = {"ok": True, "path": fp,
                              "message": f"文件已更新（+{additions} -{removals} 行）。",
                              "location": _location_of(fp)}
    if replace_all:
        result["replaced"] = replaced
        result["message"] = f"已替换全部 {replaced} 处（+{additions} -{removals} 行）。"
    return json.dumps(result, ensure_ascii=False)


def _emit_file_diff(fp: str, old_content: str, new_content: str,
                    additions: int, removals: int) -> None:
    """编辑成功后发出 diff 展示事件（过程性，不进模型上下文；webui 通道订阅）。"""
    try:
        import asyncio

        from core.event_bus import event_bus
        from core.stream_events import EVENT_FILE_DIFF
        from entities._sdk import get_current_scope
        diff = edit_utils.unified_diff(os.path.basename(fp), old_content, new_content)
        payload = {
            "scope": get_current_scope(),
            "path": fp,
            "diff": diff,
            "additions": additions,
            "removals": removals,
        }
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(event_bus.emit(EVENT_FILE_DIFF, payload))
        except RuntimeError:
            # 同步工具在 to_thread 工作线程中执行：经后台注册表绑定的主循环桥回
            from entities._sdk import get_background_registry
            registry = get_background_registry()
            loop = getattr(registry, "_loop", None) if registry else None
            if loop and loop.is_running():
                loop.call_soon_threadsafe(
                    lambda: asyncio.ensure_future(event_bus.emit(EVENT_FILE_DIFF, payload)))
    except Exception:
        pass  # 展示事件失败不影响编辑主流程


def _shell_write_check_enabled() -> bool:
    """shell 写操作预检开关（默认开）。"""
    try:
        from core.config import ConfigManager
        return bool(ConfigManager.get("sandbox_shell_write_check", True))
    except Exception:
        return True


def _suggest_similar_path(fp: str) -> str:
    """为不存在的路径给出相似文件建议（对齐 Claude Code 的 Did-you-mean）。"""
    parent = os.path.dirname(fp) or "."
    name = os.path.basename(fp)
    try:
        candidates = sorted(os.listdir(parent))[:200]
    except OSError:
        return ""
    import difflib
    close = difflib.get_close_matches(name, candidates, n=3, cutoff=0.5)
    if not close:
        return ""
    suggestions = ", ".join(os.path.join(parent, c) for c in close)
    return f"是否想编辑: {suggestions}？"


@tool(name="append_file", group="os")
def append_file(path: str, content: str) -> str:
    """追加内容到文件末尾。

    Args:
        path: 文件路径
        content: 要追加的文本内容
    """
    try:
        fp = safe_path(path)
        # 追加同样会改写文件：与 write_file/edit_file 一致接入严格 read-before-write 门
        # （不自动建基线——未读取过的已有文件须先 read_file，防止盲追加重复/错位内容；
        # 已读取后外部修改仍被 check_writable 的 mtime+内容比对捕获）
        if os.path.exists(fp):
            ok, message = file_state.check_writable(fp)
            if not ok:
                return tool_error(message, cause=ErrorCause.STATE, retryable=False, path=fp)
            # 原子追加：读原文 + 拼接 + 原子重写（追加语义下崩溃/断电不损原文）
            existing, encoding, eol = _read_text_with_metadata(fp)
            _write_text_with_metadata(fp, existing + content, encoding, eol)
        else:
            _write_text_with_metadata(fp, content, "utf-8", "LF")
        # 若缓存中有该文件的读取记录，追加后同步刷新，避免后续编辑被误判为过期
        if file_state.get_cache().get(fp) is not None:
            new_content, _, _ = _read_text_with_metadata(fp)
            file_state.record_write(fp, new_content, os.path.getmtime(fp))
        return json.dumps({"ok": True, "path": fp,
                           "location": _location_of(fp)}, ensure_ascii=False)
    except Exception as e:
        return error_from_exception(e, action="追加文件内容")


@tool(name="list_directory", group="os", concurrency_safe=True)
def list_directory(path: str = ".", recursive: bool = False, max_depth: int = 3) -> str:
    """列出目录内容。支持递归树形浏览。

    Args:
        path: 目录路径（相对于 workspace），默认 workspace 根目录
        recursive: 是否递归列出子目录
        max_depth: 递归最大深度，默认 3
    """
    try:
        fp = safe_path(path)
        if not os.path.isdir(fp):
            return tool_error(f"不是有效目录: {path}", cause=ErrorCause.PARAM,
                              retryable=False,
                              hint="请确认路径存在且为目录（可用 file_info 检查）")

        if recursive:
            tree = _build_tree(fp, max_depth, 0)
            return json.dumps({"path": fp, "tree": tree}, ensure_ascii=False)

        items: List[Dict[str, Any]] = []
        for name in sorted(os.listdir(fp)):
            full = os.path.join(fp, name)
            entry: Dict[str, Any] = {"name": name, "path": full}
            if os.path.isdir(full):
                entry["type"] = "dir"
            else:
                entry["type"] = "file"
                try:
                    entry["size"] = os.path.getsize(full)
                except OSError:
                    log("list_directory 异常已忽略", "DEBUG")
            items.append(entry)
        return json.dumps({"path": fp, "count": len(items), "items": items[:200]}, ensure_ascii=False)
    except Exception as e:
        return error_from_exception(e, action="列出目录")


def _build_tree(dir_path: str, max_depth: int, depth: int) -> List[Dict[str, Any]]:
    """递归构建目录树。"""
    items: List[Dict[str, Any]] = []
    try:
        for name in sorted(os.listdir(dir_path)):
            full = os.path.join(dir_path, name)
            entry: Dict[str, Any] = {"name": name, "path": full}
            if os.path.isdir(full):
                entry["type"] = "dir"
                if depth < max_depth:
                    entry["children"] = _build_tree(full, max_depth, depth + 1)
                else:
                    entry["children"] = "..."
            else:
                entry["type"] = "file"
                try:
                    entry["size"] = os.path.getsize(full)
                except OSError:
                    log("_build_tree 异常已忽略", "DEBUG")
            items.append(entry)
    except PermissionError:
        log("_build_tree 异常已忽略", "DEBUG")
    return items


@tool(name="file_info", group="os", concurrency_safe=True)
def file_info(path: str) -> str:
    """获取文件或目录的详细信息（存在性、类型、大小、修改时间）。

    Args:
        path: 文件或目录路径
    """
    try:
        fp = safe_path(path)
        e = os.path.exists(fp)
        info: Dict[str, Any] = {"path": path, "resolved": fp, "exists": e}
        if e:
            info["is_file"] = os.path.isfile(fp)
            info["is_dir"] = os.path.isdir(fp)
            try:
                stat = os.stat(fp)
                info["size"] = stat.st_size
                info["modified"] = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(stat.st_mtime))
            except OSError:
                log("file_info 异常已忽略", "DEBUG")
        return json.dumps(info, ensure_ascii=False)
    except Exception as e:
        return error_from_exception(e, action="获取文件信息")


@tool(name="copy_file", group="os")
def copy_file(src: str, dst: str) -> str:
    """复制文件。

    Args:
        src: 源文件路径
        dst: 目标文件路径
    """
    try:
        import shutil
        src_fp = safe_path(src)
        dst_fp = safe_path(dst)
        if not os.path.isfile(src_fp):
            return tool_error(f"源文件不存在: {src}", cause=ErrorCause.NOT_FOUND,
                              retryable=False)
        os.makedirs(os.path.dirname(dst_fp) or ".", exist_ok=True)
        shutil.copy2(src_fp, dst_fp)
        return json.dumps({"ok": True, "src": src_fp, "dst": dst_fp,
                           "location": _location_of(dst_fp)}, ensure_ascii=False)
    except Exception as e:
        return error_from_exception(e, action="复制文件")


@tool(name="move_file", group="os")
def move_file(src: str, dst: str) -> str:
    """移动或重命名文件。

    Args:
        src: 源文件路径
        dst: 目标文件路径
    """
    try:
        import shutil
        src_fp = safe_path(src)
        dst_fp = safe_path(dst)
        if not os.path.exists(src_fp):
            return tool_error(f"源路径不存在: {src}", cause=ErrorCause.NOT_FOUND,
                              retryable=False)
        os.makedirs(os.path.dirname(dst_fp) or ".", exist_ok=True)
        shutil.move(src_fp, dst_fp)
        return json.dumps({"ok": True, "src": src_fp, "dst": dst_fp,
                           "location": _location_of(dst_fp)}, ensure_ascii=False)
    except Exception as e:
        return error_from_exception(e, action="移动文件")


@tool(name="delete_file", group="os")
def delete_file(path: str) -> str:
    """删除文件（不删除目录）。

    Args:
        path: 要删除的文件路径
    """
    try:
        fp = safe_path(path)
        if not os.path.isfile(fp):
            return tool_error(f"文件不存在或不是文件: {path}",
                              cause=ErrorCause.NOT_FOUND, retryable=False)
        os.remove(fp)
        return json.dumps({"ok": True, "deleted": fp,
                           "location": _location_of(fp)}, ensure_ascii=False)
    except Exception as e:
        return error_from_exception(e, action="删除文件")


@tool(name="mkdir", group="os")
def mkdir(path: str) -> str:
    """创建目录（递归创建父目录）。

    Args:
        path: 目录路径
    """
    try:
        fp = safe_path(path)
        os.makedirs(fp, exist_ok=True)
        return json.dumps({"ok": True, "path": fp,
                           "location": _location_of(fp)}, ensure_ascii=False)
    except Exception as e:
        return error_from_exception(e, action="创建目录")


@tool(name="search_files", group="os", concurrency_safe=True)
def search_files(path: str = ".", pattern: str = "*", content_pattern: str = "",
                 max_results: int = 50) -> str:
    """搜索文件：按 glob 模式找文件名（任意深度），或按正则搜索文件内容（类似 grep）。

    自动跳过噪声目录（node_modules/.git/__pycache__ 等，search_exclude_dirs 可配置）；
    内容模式额外跳过二进制与超大文件（>2MB）。

    Args:
        path: 搜索根目录（相对于 workspace）
        pattern: 文件名 glob 模式，如 '*.png'、'config/*.json'（任意深度匹配）
        content_pattern: 文件内容正则（可选）。提供时返回匹配的文件及命中行，
            如 'def \\w+\\('、'TODO'
        max_results: 最大返回数量，默认 50
    """
    try:
        fp = safe_path(path)
        if not os.path.isdir(fp):
            return tool_error(f"不是有效目录: {path}", cause=ErrorCause.PARAM,
                              retryable=False,
                              hint="请确认路径存在且为目录（可用 file_info 检查）")

        from entities.filesystem.scan import (
            content_search,
            iter_matches,
            resolve_exclude_dirs,
        )
        exclude = resolve_exclude_dirs()

        if not content_pattern:
            # 按修改时间倒序（最近修改在前）；
            # 目录沉底——mtime 排序对目录无操作价值。path 保持绝对路径
            # （可直接传给 read_file 等工具）
            entries = sorted(
                iter_matches(fp, pattern, exclude),
                key=lambda e: (not e.is_dir, -e.mtime),
            )
            matches = [
                {"path": os.path.normpath(e.abspath), "name": os.path.basename(e.abspath),
                 "type": "dir" if e.is_dir else "file", "size": e.size}
                for e in entries[:max_results]
            ]
            return json.dumps({
                "pattern": pattern,
                "root": path,
                "excluded_dirs": sorted(exclude),
                "count": len(matches),
                "results": matches,
            }, ensure_ascii=False)

        # 内容搜索模式（grep 语义）
        import re
        try:
            regex = re.compile(content_pattern)
        except re.error as e:
            return tool_error(f"无效的正则表达式: {e}", cause=ErrorCause.PARAM,
                              retryable=False)

        hits = content_search(fp, pattern, regex, exclude, max_results=max_results)
        return json.dumps({
            "pattern": pattern,
            "content_pattern": content_pattern,
            "root": path,
            "count": len(hits),
            "results": [
                {"path": os.path.normpath(h.abspath), "matches": h.lines} for h in hits
            ],
        }, ensure_ascii=False)
    except Exception as e:
        return error_from_exception(e, action="搜索文件")


# ------------------------------------------------------------------
# Shell / Python 执行
# ------------------------------------------------------------------


def _redundant_workspace_prefix(command: str) -> Optional[str]:
    """返回命令中误带 workspace 目录名前缀的相对路径 token。

    cwd 已是 workspace 根目录，该前缀会指向不存在的嵌套路径（workspace/workspace/...）。
    仅用于失败归因提示，不做拦截；无命中返回 None。
    """
    prefix = os.path.basename(os.path.abspath(_WORKSPACE)) + "/"
    try:
        tokens = shlex.split(command, posix=True)
    except ValueError:
        tokens = command.split()
    for token in tokens:
        if token.startswith(prefix):
            return token
    return None


# 形如记忆索引键的相对路径（memory/*.md）：recall 结果 file 来源的标注形态，
# 相对数据目录父目录而非 Shell 工作目录
_MEMORY_KEY_RE = re.compile(r"^memory/[\w./\-]+\.md$")

# Python 缺失模块错误（No module named xxx / ModuleNotFoundError: No module named 'xxx'）
_MISSING_MODULE_RE = re.compile(r"No module named '?[\w.]+'?")


def _memory_key_token(command: str) -> Optional[str]:
    """返回命令中形如记忆索引键（memory/*.md）的相对路径 token。

    仅用于失败归因提示，不做拦截；无命中返回 None。
    """
    try:
        tokens = shlex.split(command, posix=True)
    except ValueError:
        tokens = command.split()
    for token in tokens:
        if _MEMORY_KEY_RE.match(token):
            return token
    return None


def _memory_key_note(key: str) -> str:
    """记忆索引键误用为 Shell 相对路径的归因提示（事实陈述：键空间与真实位置）。"""
    from core.path import ConfigPaths
    root = os.path.dirname(os.path.abspath(ConfigPaths.MEMORY_DIR))
    real = os.path.join(root, key)
    base = f"注意: {key} 是记忆索引键（相对数据目录 {root}，非 Shell 相对路径）"
    if os.path.isfile(real):
        return f"{base}，实际文件在 {real}"
    return base


def _missing_module_hint(stdout: str, stderr: str) -> Optional[str]:
    """uv 管理环境下缺失模块错误的环境事实提示（当前解释器为 uv venv、不含 pip）。

    只陈述事实不做操作建议；非 uv 环境无此事实差异，不提示。无命中返回 None。
    """
    if not _MISSING_MODULE_RE.search(f"{stdout}\n{stderr}"):
        return None
    import shutil

    from entities.system.python_service import detect_env_manager
    python3 = shutil.which("python3") or sys.executable
    if detect_env_manager(python3).get("manager") != "uv":
        return None
    return "注意: 当前 python3 是 uv 创建的 venv（不含 pip）"


@tool(name="run_shell_command", group="os", tags=["always"], description=_SHELL_PROMPT)
def run_shell_command(command: str, timeout: int = 0, run_in_background: bool = False) -> str:
    """在系统 shell 中执行命令并返回输出结果。

    每次命令在独立进程中执行（shell 状态不持久），但工作目录在命令间持久
    （可用 cd 切换，对后续命令生效）；沙箱开启时工作目录被限制在 workspace 内，漂出自动重置。
    输出超过 30000 字符时完整内容自动落盘，返回预览和文件路径。

    工具偏好：搜文件用 search_files（而非 find/ls）、读文件用 read_file（而非 cat/head）、
    编辑文件用 edit_file（而非 sed/awk）、写文件用 write_file（而非 echo >/cat <<EOF）。

    Args:
        command: 要执行的 shell 命令字符串
        timeout: 预期时长（秒）。0 = 自动（前台默认 120，后台默认 1800）。
            前台到时返回失败结果；后台超过该时长系统提醒你并附最新进度
            （不自动终止），是否终止由你决定
        run_in_background: 是否后台执行（构建/训练等长任务）。立即返回任务 ID
            和输出文件路径，完成后系统自动通知；期间可用 read_file 查看进度
    """
    # 容忍模型传入字符串形式的布尔值
    run_in_background = coerce_bool_arg(run_in_background, False)
    try:
        from core.command import run_command
        from entities.filesystem import shell_state

        _load_config()

        # 沙箱预检：拦截漂出 workspace 的写操作（配置 sandbox_shell_write_check 可关）
        if _SANDBOX and _shell_write_check_enabled():
            from entities.filesystem.shell_guard import check_command_safety
            violation = check_command_safety(command, _WORKSPACE)
            if violation:
                return tool_error(
                    f"沙箱拦截: {violation}。"
                    "沙箱开启时不允许向 workspace 外写入。"
                    "请改用 workspace 内路径，或由管理员关闭沙箱/该检查。",
                    cause=ErrorCause.PERMISSION, retryable=False,
                    sandbox_violation=True,
                )

        cwd = shell_state.get_cwd(_WORKSPACE, sandbox=_SANDBOX)

        if run_in_background:
            # 0 = 自动：后台缺省预期时长由 launch_background 读
            # background_shell_alert_after 决定（超时提醒语义，不终止进程）
            return json.dumps(
                launch_background(
                    command, cwd, _WORKSPACE,
                    timeout_sec=float(timeout) if timeout > 0 else 0.0,
                ),
                ensure_ascii=False,
            )

        # 前台缺省沿用历史默认 120s；显式值钳制在 1s~24h
        timeout = 120 if timeout <= 0 else min(max(1, int(timeout)), 86400)

        pwd_file = ""
        run_cmd = command
        is_posix = os.name != "nt"
        if is_posix:
            run_cmd, pwd_file = shell_state.wrap_command_capture_pwd(command)

        # 环境变量卫生（NO_COLOR/pager/locale）由 core.command.run_command 统一注入
        result = run_command(run_cmd, timeout_sec=timeout, cwd=cwd)

        notes: List[str] = []
        if is_posix:
            captured = shell_state.read_captured_pwd(pwd_file)
            if captured and shell_state.set_cwd(captured, _WORKSPACE, sandbox=_SANDBOX):
                notes.append("注意: 工作目录已重置回 workspace 根目录（沙箱不允许漂出）")

        stdout = result.stdout.strip()
        stderr = result.stderr.strip() if result.stderr else ""
        stdout, persisted = shell_state.truncate_or_persist(stdout, _WORKSPACE)
        if len(stderr) > 2000:
            stderr = stderr[:2000] + "\n... (stderr 已截断)"

        payload: Dict[str, Any] = {"ok": result.ok, "stdout": stdout, "stderr": stderr}
        if persisted:
            payload["persisted"] = persisted
        # 失败时附带真实 cwd 与工作区根，便于模型定位路径问题后自纠（而非猜测系统路径）
        if not result.ok:
            # 退出码帮助模型区分否定结果与真实错误（如 grep: 1=无匹配, 2=用法错误）
            if result.returncode is not None:
                payload["returncode"] = result.returncode
            # 非零码 + 无错误输出的语义提示（grep 无匹配/条件不成立 vs 真实错误，
            # 避免模型把否定结果当故障盲目重试或谎报失败）
            if not stderr:
                notes.append(
                    "注意: 命令以非零码结束但无错误输出——若是 grep/搜索/测试类命令，"
                    "这通常表示无匹配或条件不成立，不是执行失败，无需重试"
                )
            payload["cwd"] = shell_state.get_cwd(_WORKSPACE, sandbox=_SANDBOX)
            payload["workspace_root"] = os.path.abspath(_WORKSPACE)
            redundant = _redundant_workspace_prefix(command)
            if redundant:
                prefix = os.path.basename(os.path.abspath(_WORKSPACE)) + "/"
                stripped = redundant
                while stripped.startswith(prefix):
                    stripped = stripped[len(prefix):]
                notes.append(
                    f"注意: 工作目录已是 workspace 根目录，{redundant} 的 {prefix} 前缀多余"
                    f"（指向不存在的嵌套路径），直接写 {stripped.rstrip('/') or '.'} 即可"
                )
            # 记忆索引键误用为 Shell 相对路径：cwd 下不存在才归因（存在则失败另有原因）
            key = _memory_key_token(command)
            if key and not os.path.exists(os.path.join(str(payload["cwd"]), key)):
                notes.append(_memory_key_note(key))
            module_hint = _missing_module_hint(stdout, stderr)
            if module_hint:
                notes.append(module_hint)
        if notes:
            payload["notes"] = notes
        return json.dumps(payload, ensure_ascii=False)
    except Exception as e:
        return error_from_exception(e, action="执行 shell 命令")


@tool(name="python_exec", group="os")
def python_exec(code: str, timeout: int = 30) -> str:
    """执行 Python 代码片段并返回输出结果，适合数据计算、文本处理等场景。

    注意：本工具直接启动 Python 进程，不经过 run_shell_command 的 shell 写预检；
    沙箱开启时子进程工作目录被限定在 workspace 根目录。

    Args:
        code: 要执行的 Python 代码
        timeout: 超时时间（秒），默认 30
    """
    import subprocess
    import sys
    try:
        _load_config()
        timeout = max(1, int(timeout))
        run_kwargs: Dict[str, Any] = {}
        if _SANDBOX:
            ws_abs = os.path.abspath(_WORKSPACE)
            os.makedirs(ws_abs, exist_ok=True)
            run_kwargs["cwd"] = ws_abs
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True, text=True, timeout=timeout,
            **run_kwargs,
        )
        stdout = result.stdout.strip()
        stderr = result.stderr.strip()
        # 输出落盘：stdout 与 run_shell_command 同一阈值（超 30000 字符落盘
        # 返回 persisted 路径，模型可 read_file 分段取回，不再截断丢弃）；
        # stderr 小限截断（多为回溯/警告，完整价值低）
        from entities.filesystem import shell_state
        stdout, persisted_path = shell_state.truncate_or_persist(
            stdout, os.path.abspath(_WORKSPACE),
        )
        if len(stderr) > 1000:
            stderr = stderr[:1000] + "\n... (截断)"
        payload: Dict[str, Any] = {
            "ok": result.returncode == 0,
            "stdout": stdout,
            "stderr": stderr,
            "returncode": result.returncode,
        }
        if persisted_path:
            payload["persisted"] = persisted_path
        return json.dumps(payload, ensure_ascii=False)
    except subprocess.TimeoutExpired:
        return tool_error(f"执行超时 ({timeout}s)", cause=ErrorCause.TIMEOUT,
                          retryable=True)
    except Exception as e:
        return error_from_exception(e, action="执行 Python 代码")

# 挂载同实体的 notebook 工具（discover_entities 只导入 tools.py）
from core.log import log
from entities.filesystem import notebook as _notebook  # noqa: F401,E402
