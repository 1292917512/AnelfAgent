"""操作系统实体 — 文件操作 + Shell/Python 执行。

文件路径操作受沙箱保护，默认限制在 workspace/ 目录下。
沙箱通过 app_config.json 中的 workspace_root 和 sandbox_enabled 配置。
"""

from __future__ import annotations

import glob
import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List

from entities._sdk import tool, entity

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
# 工具
# ------------------------------------------------------------------


@tool(name="read_file", group="os", tags=["media:file"])
def read_file(path: str, encoding: str = "utf-8") -> str:
    """读取文本文件内容。大文件自动截断至 10000 字符。二进制文件返回文件信息。

    Args:
        path: 文件路径（相对于 workspace 或绝对路径）
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
        with open(fp, "r", encoding=encoding) as f:
            content = f.read()
        if len(content) > 10000:
            return content[:10000] + f"\n\n... (已截断，共 {len(content)} 字符)"
        return content
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


@tool(name="write_file", group="os")
def write_file(path: str, content: str) -> str:
    """写入文件（覆盖）。目录不存在时自动创建。

    Args:
        path: 文件路径（相对于 workspace）
        content: 要写入的文本内容
    """
    try:
        fp = _safe_path(path)
        os.makedirs(os.path.dirname(fp) or ".", exist_ok=True)
        with open(fp, "w", encoding="utf-8") as f:
            f.write(content)
        return json.dumps({"ok": True, "path": fp, "size": len(content)}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


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
        return json.dumps({"ok": True, "path": fp}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


@tool(name="list_directory", group="os")
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


@tool(name="file_info", group="os")
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


@tool(name="search_files", group="os")
def search_files(path: str = ".", pattern: str = "*", max_results: int = 50) -> str:
    """在目录树中按 glob 模式搜索文件。

    Args:
        path: 搜索根目录（相对于 workspace）
        pattern: glob 模式，如 '*.png'、'**/*.json'
        max_results: 最大返回数量，默认 50
    """
    try:
        fp = _safe_path(path)
        if not os.path.isdir(fp):
            return json.dumps({"error": f"不是有效目录: {path}"}, ensure_ascii=False)

        search_pattern = os.path.join(fp, pattern)
        matches: List[Dict[str, Any]] = []
        for match in glob.iglob(search_pattern, recursive=True):
            entry: Dict[str, Any] = {"path": os.path.normpath(match), "name": os.path.basename(match)}
            if os.path.isdir(match):
                entry["type"] = "dir"
            else:
                entry["type"] = "file"
                try:
                    entry["size"] = os.path.getsize(match)
                except OSError:
                    pass
            matches.append(entry)
            if len(matches) >= max_results:
                break

        return json.dumps({
            "pattern": pattern,
            "root": path,
            "count": len(matches),
            "results": matches,
        }, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


# ------------------------------------------------------------------
# Shell / Python 执行
# ------------------------------------------------------------------


@tool(name="run_shell_command", group="os")
def run_shell_command(command: str, timeout: int = 30) -> str:
    """在系统 shell 中执行命令并返回输出结果。

    Args:
        command: 要执行的 shell 命令字符串
        timeout: 超时时间（秒），默认 30
    """
    try:
        from core.command import run_command
        result = run_command(command, timeout_sec=timeout)
        output = result.stdout.strip()
        if len(output) > 5000:
            output = output[:5000] + "\n... (输出过长，已截断)"
        return json.dumps({
            "ok": result.ok,
            "stdout": output,
            "stderr": result.stderr.strip()[:1000] if result.stderr else "",
        }, ensure_ascii=False)
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
