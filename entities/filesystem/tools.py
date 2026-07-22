"""操作系统实体 — 文件操作 + Shell/Python 执行。

文件路径操作受沙箱保护，默认限制在 workspace/ 目录下。
沙箱通过 app_config.json 中的 workspace_root 和 sandbox_enabled 配置。

edit_file/read_file/write_file 的编辑安全语义移植自 Claude Code
（read-before-write、mtime 过期检查、弯引号容忍匹配、行尾往返），
详见 docs/refactor/01-claudecode-tools.md。
"""

from __future__ import annotations

import glob
import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Tuple

from entities._sdk import tool, entity
from entities.filesystem import edit_utils, file_state

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


def _safe_path(path: str) -> str:
    """解析路径并执行沙箱检查。相对路径基于 workspace_root 解析。"""
    _load_config()
    ws_abs = os.path.abspath(_WORKSPACE)
    os.makedirs(ws_abs, exist_ok=True)

    if os.path.isabs(path):
        resolved = os.path.normpath(path)
    else:
        # Strip workspace prefix if already present to avoid double-nesting
        norm = os.path.normpath(path)
        ws_norm = os.path.normpath(_WORKSPACE)
        if norm.startswith(ws_norm + os.sep) or norm == ws_norm:
            norm = norm[len(ws_norm):].lstrip(os.sep)
        resolved = os.path.normpath(os.path.join(ws_abs, norm))

    if _SANDBOX and not resolved.startswith(ws_abs):
        raise ValueError(f"沙箱限制: {path} 不在工作目录 ({_WORKSPACE}) 内")
    return resolved


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
- 除非用户要求，不要在代码中添加 emoji。"""

_SHELL_PROMPT = """在系统 shell 中执行命令并返回输出结果。

每次命令在独立进程中执行（shell 状态不持久，环境变量/alias 不保留），
但工作目录在命令间持久（cd 对后续命令生效）；沙箱开启时漂出 workspace 自动重置。
输出超过 30000 字符时完整内容自动落盘，返回预览和文件路径（用 read_file 查看）。
超时默认 120 秒，最大 600 秒。

工具偏好（不要用 shell 做这些事）:
- 搜索文件: 用 search_files（而非 find/ls）
- 读取文件: 用 read_file（而非 cat/head/tail）
- 编辑文件: 用 edit_file（而非 sed/awk）
- 写入文件: 用 write_file（而非 echo > 或 cat <<EOF）
- 路径含空格务必加引号；避免使用 cd 进入无关目录。"""


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
    """按原编码与行尾风格写回文件（CRLF 文件写回 CRLF）。"""
    if eol == "CRLF":
        content = content.replace("\r\n", "\n").replace("\n", "\r\n")
    with open(fp, "wb") as f:
        f.write(content.encode(encoding))


def _add_line_numbers(content: str, start_line: int = 1) -> str:
    """添加行号前缀（格式: 行号→内容）。前缀不是文件内容，编辑时不得包含。"""
    lines = content.split("\n")
    width = len(str(start_line + len(lines) - 1))
    return "\n".join(f"{i:>{width}}→{line}" for i, line in enumerate(lines, start_line))


@tool(name="read_file", group="os", tags=["media:file"], concurrency_safe=True, description=_READ_FILE_PROMPT)
def read_file(path: str, offset: int = 0, limit: int = 0, encoding: str = "utf-8") -> str:
    """读取文本文件内容，带行号输出（格式: 行号→内容）。大文件请用 offset/limit 分段读取。

    Args:
        path: 文件路径（相对于 workspace 或绝对路径）
        offset: 起始行号（从 1 开始），0 表示从头读取
        limit: 最多读取行数，0 表示读取到上限（2000 行）
        encoding: 文件编码，默认 utf-8
    """
    try:
        fp = _safe_path(path)
        if not os.path.isfile(fp):
            return json.dumps({"error": f"文件不存在: {path}", "resolved": fp}, ensure_ascii=False)
        # Binary files: return metadata instead of trying to decode
        bin_exts = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".ico",
                    ".mp3", ".wav", ".ogg", ".flac", ".m4a", ".opus", ".amr",
                    ".mp4", ".avi", ".mkv", ".mov", ".webm",
                    ".zip", ".tar", ".gz", ".7z", ".rar",
                    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
                    ".exe", ".dll", ".so", ".bin", ".dat", ".sqlite3"}
        ext = os.path.splitext(fp)[1].lower()
        if ext in bin_exts:
            size = os.path.getsize(fp)
            return json.dumps({
                "type": "binary",
                "path": fp,
                "size": size,
                "ext": ext,
                "hint": "Use recognize_image for images, voice_to_text for audio",
            }, ensure_ascii=False)

        size = os.path.getsize(fp)
        if size > _READ_MAX_BYTES and offset <= 0 and limit <= 0:
            return json.dumps({
                "error": f"文件过大（{size} 字节，上限 {_READ_MAX_BYTES}）。"
                         "请使用 offset/limit 参数分段读取。",
                "path": fp,
            }, ensure_ascii=False)

        content, _, _ = _read_text_with_metadata(fp, encoding)
        mtime = os.path.getmtime(fp)
        start_line = max(1, offset) if offset > 0 else 1
        max_lines = limit if limit > 0 else _READ_MAX_LINES
        is_full_read = offset <= 0 and limit <= 0

        # 读重去重：相同范围且文件未变 → 返回存根（对齐 Claude Code Read 去重）
        cached = file_state.get_cache().get(fp)
        if cached is not None and mtime <= cached.mtime:
            same_range = (is_full_read and not cached.is_partial_view) or (
                cached.offset == (offset or None) and cached.limit == (limit or None))
            if same_range:
                return json.dumps({
                    "unchanged": True,
                    "path": fp,
                    "message": "文件自上次读取后未变化，本次会话中此前的读取结果仍然有效，"
                               "请直接参考，不必重复读取。",
                }, ensure_ascii=False)

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

        numbered = _add_line_numbers(body, start_line)
        tail_notes: List[str] = []
        end_line = start_line + len(selected) - 1
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
            offset=None if is_full_read else offset,
            limit=None if is_full_read else limit,
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
        return json.dumps({"error": str(e)}, ensure_ascii=False)


@tool(name="write_file", group="os", description=_WRITE_FILE_PROMPT)
def write_file(path: str, content: str) -> str:
    """写入文件（覆盖）。目录不存在时自动创建。修改已有文件前必须先用 read_file 读取；
    对已有文件的局部修改请优先使用 edit_file（只发送差异部分）。

    Args:
        path: 文件路径（相对于 workspace）
        content: 要写入的文本内容
    """
    try:
        fp = _safe_path(path)
        if os.path.exists(fp):
            ok, message = file_state.check_writable(fp)
            if not ok:
                return json.dumps({"error": message, "path": fp}, ensure_ascii=False)
        os.makedirs(os.path.dirname(fp) or ".", exist_ok=True)
        with open(fp, "w", encoding="utf-8") as f:
            f.write(content)
        file_state.record_write(fp, content.replace("\r\n", "\n"), os.path.getmtime(fp))
        return json.dumps({"ok": True, "path": fp, "size": len(content)}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


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
    if isinstance(replace_all, str):
        replace_all = replace_all.strip().lower() in ("true", "1", "yes")
    try:
        fp = _safe_path(file_path)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)

    def _err(message: str, code: int) -> str:
        return json.dumps({"error": message, "code": code}, ensure_ascii=False)

    if old_string == new_string:
        return _err("未做任何修改：old_string 与 new_string 完全相同。", 1)

    exists = os.path.isfile(fp)
    if not exists:
        if old_string == "":
            # 空 old_string + 文件不存在 = 创建新文件
            try:
                os.makedirs(os.path.dirname(fp) or ".", exist_ok=True)
                _write_text_with_metadata(fp, new_string, "utf-8", "LF")
                file_state.record_write(fp, new_string, os.path.getmtime(fp))
                return json.dumps({"ok": True, "path": fp,
                                   "message": f"文件创建成功: {fp}"}, ensure_ascii=False)
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
        return _err(message, 6)

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
    replaced = occurrences if replace_all else 1
    result: Dict[str, Any] = {"ok": True, "path": fp,
                              "message": f"文件已更新（+{additions} -{removals} 行）。"}
    if replace_all:
        result["replaced"] = replaced
        result["message"] = f"已替换全部 {replaced} 处（+{additions} -{removals} 行）。"
    return json.dumps(result, ensure_ascii=False)


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
        fp = _safe_path(path)
        os.makedirs(os.path.dirname(fp) or ".", exist_ok=True)
        with open(fp, "a", encoding="utf-8") as f:
            f.write(content)
        # 若缓存中有该文件的读取记录，追加后同步刷新，避免后续编辑被误判为过期
        if file_state.get_cache().get(fp) is not None:
            new_content, _, _ = _read_text_with_metadata(fp)
            file_state.record_write(fp, new_content, os.path.getmtime(fp))
        return json.dumps({"ok": True, "path": fp}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


@tool(name="list_directory", group="os", concurrency_safe=True)
def list_directory(path: str = ".", recursive: bool = False, max_depth: int = 3) -> str:
    """列出目录内容。支持递归树形浏览。

    Args:
        path: 目录路径（相对于 workspace），默认 workspace 根目录
        recursive: 是否递归列出子目录
        max_depth: 递归最大深度，默认 3
    """
    try:
        fp = _safe_path(path)
        if not os.path.isdir(fp):
            return json.dumps({"error": f"不是有效目录: {path}"}, ensure_ascii=False)

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
                    pass
            items.append(entry)
        return json.dumps({"path": fp, "count": len(items), "items": items[:200]}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


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
                    pass
            items.append(entry)
    except PermissionError:
        pass
    return items


@tool(name="file_info", group="os", concurrency_safe=True)
def file_info(path: str) -> str:
    """获取文件或目录的详细信息（存在性、类型、大小、修改时间）。

    Args:
        path: 文件或目录路径
    """
    try:
        fp = _safe_path(path)
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
                pass
        return json.dumps(info, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


@tool(name="copy_file", group="os")
def copy_file(src: str, dst: str) -> str:
    """复制文件。

    Args:
        src: 源文件路径
        dst: 目标文件路径
    """
    try:
        import shutil
        src_fp = _safe_path(src)
        dst_fp = _safe_path(dst)
        if not os.path.isfile(src_fp):
            return json.dumps({"error": f"源文件不存在: {src}"}, ensure_ascii=False)
        os.makedirs(os.path.dirname(dst_fp) or ".", exist_ok=True)
        shutil.copy2(src_fp, dst_fp)
        return json.dumps({"ok": True, "src": src, "dst": dst}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


@tool(name="move_file", group="os")
def move_file(src: str, dst: str) -> str:
    """移动或重命名文件。

    Args:
        src: 源文件路径
        dst: 目标文件路径
    """
    try:
        import shutil
        src_fp = _safe_path(src)
        dst_fp = _safe_path(dst)
        if not os.path.exists(src_fp):
            return json.dumps({"error": f"源路径不存在: {src}"}, ensure_ascii=False)
        os.makedirs(os.path.dirname(dst_fp) or ".", exist_ok=True)
        shutil.move(src_fp, dst_fp)
        return json.dumps({"ok": True, "src": src, "dst": dst}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


@tool(name="delete_file", group="os")
def delete_file(path: str) -> str:
    """删除文件（不删除目录）。

    Args:
        path: 要删除的文件路径
    """
    try:
        fp = _safe_path(path)
        if not os.path.isfile(fp):
            return json.dumps({"error": f"文件不存在或不是文件: {path}"}, ensure_ascii=False)
        os.remove(fp)
        return json.dumps({"ok": True, "deleted": path}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


@tool(name="mkdir", group="os")
def mkdir(path: str) -> str:
    """创建目录（递归创建父目录）。

    Args:
        path: 目录路径
    """
    try:
        fp = _safe_path(path)
        os.makedirs(fp, exist_ok=True)
        return json.dumps({"ok": True, "path": fp}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


@tool(name="search_files", group="os", concurrency_safe=True)
def search_files(path: str = ".", pattern: str = "*", content_pattern: str = "",
                 max_results: int = 50) -> str:
    """搜索文件：按 glob 模式找文件名，或按正则搜索文件内容（类似 grep）。

    Args:
        path: 搜索根目录（相对于 workspace）
        pattern: 文件名 glob 模式，如 '*.png'、'**/*.json'
        content_pattern: 文件内容正则（可选）。提供时返回匹配的文件及命中行，
            如 'def \\w+\\('、'TODO'
        max_results: 最大返回数量，默认 50
    """
    try:
        fp = _safe_path(path)
        if not os.path.isdir(fp):
            return json.dumps({"error": f"不是有效目录: {path}"}, ensure_ascii=False)

        search_pattern = os.path.join(fp, pattern)
        candidates = [m for m in glob.iglob(search_pattern, recursive=True)]

        if not content_pattern:
            matches: List[Dict[str, Any]] = []
            for match in candidates:
                entry: Dict[str, Any] = {"path": os.path.normpath(match),
                                         "name": os.path.basename(match)}
                if os.path.isdir(match):
                    entry["type"] = "dir"
                else:
                    entry["type"] = "file"
                    try:
                        entry["size"] = os.path.getsize(match)
                        entry["mtime"] = os.path.getmtime(match)
                    except OSError:
                        pass
                matches.append(entry)
            # 按修改时间倒序（最近修改在前，对齐 Claude Code Glob 语义）
            matches.sort(key=lambda e: e.get("mtime", 0), reverse=True)
            for entry in matches:
                entry.pop("mtime", None)
            matches = matches[:max_results]
            return json.dumps({
                "pattern": pattern,
                "root": path,
                "count": len(matches),
                "results": matches,
            }, ensure_ascii=False)

        # 内容搜索模式（grep 语义）
        import re
        try:
            regex = re.compile(content_pattern)
        except re.error as e:
            return json.dumps({"error": f"无效的正则表达式: {e}"}, ensure_ascii=False)

        results: List[Dict[str, Any]] = []
        for match in candidates:
            if not os.path.isfile(match):
                continue
            try:
                with open(match, "r", encoding="utf-8", errors="replace") as f:
                    hit_lines = [
                        f"{i}:{line.rstrip()[:200]}"
                        for i, line in enumerate(f, 1)
                        if regex.search(line)
                    ][:5]
            except OSError:
                continue
            if hit_lines:
                results.append({"path": os.path.normpath(match), "matches": hit_lines})
                if len(results) >= max_results:
                    break

        return json.dumps({
            "pattern": pattern,
            "content_pattern": content_pattern,
            "root": path,
            "count": len(results),
            "results": results,
        }, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


# ------------------------------------------------------------------
# Shell / Python 执行
# ------------------------------------------------------------------


@tool(name="run_shell_command", group="os", tags=["always"], description=_SHELL_PROMPT)
def run_shell_command(command: str, timeout: int = 120) -> str:
    """在系统 shell 中执行命令并返回输出结果。

    每次命令在独立进程中执行（shell 状态不持久），但工作目录在命令间持久
    （可用 cd 切换，对后续命令生效）；沙箱开启时工作目录被限制在 workspace 内，漂出自动重置。
    输出超过 30000 字符时完整内容自动落盘，返回预览和文件路径。

    工具偏好：搜文件用 search_files（而非 find/ls）、读文件用 read_file（而非 cat/head）、
    编辑文件用 edit_file（而非 sed/awk）、写文件用 write_file（而非 echo >/cat <<EOF）。

    Args:
        command: 要执行的 shell 命令字符串
        timeout: 超时时间（秒），默认 120，最大 600
    """
    try:
        from core.command import run_command
        from entities.filesystem import shell_state

        _load_config()
        timeout = max(1, min(int(timeout), 600))
        cwd = shell_state.get_cwd(_WORKSPACE, sandbox=_SANDBOX)

        pwd_file = ""
        run_cmd = command
        is_posix = os.name != "nt"
        if is_posix:
            run_cmd, pwd_file = shell_state.wrap_command_capture_pwd(command)

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
        if notes:
            payload["notes"] = notes
        return json.dumps(payload, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


@tool(name="python_exec", group="os")
def python_exec(code: str, timeout: int = 30) -> str:
    """执行 Python 代码片段并返回输出结果，适合数据计算、文本处理等场景。

    Args:
        code: 要执行的 Python 代码
        timeout: 超时时间（秒），默认 30
    """
    import subprocess
    import sys
    try:
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True, text=True, timeout=timeout,
        )
        stdout = result.stdout.strip()
        stderr = result.stderr.strip()
        if len(stdout) > 5000:
            stdout = stdout[:5000] + "\n... (输出过长，已截断)"
        if len(stderr) > 1000:
            stderr = stderr[:1000] + "\n... (截断)"
        return json.dumps({
            "ok": result.returncode == 0,
            "stdout": stdout,
            "stderr": stderr,
            "returncode": result.returncode,
        }, ensure_ascii=False)
    except subprocess.TimeoutExpired:
        return json.dumps({"error": f"执行超时 ({timeout}s)"}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": f"{type(e).__name__}: {e}"}, ensure_ascii=False)
