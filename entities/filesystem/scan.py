"""文件扫描原语 — 噪声剪枝的目录遍历、glob 匹配、内容检索与二进制嗅探。

从 tools.search_files / tools.read_file 抽出的无副作用核心，职责：

- **噪声剪枝**（对齐 dsh glob 的 VCS 目录排除，扩展到依赖/缓存目录）：
  os.walk 期间按目录名剪枝，``node_modules``/``.git`` 等既不进结果也不再
  向下遍历——此前 glob.iglob 全量下钻，装了依赖的 workspace 一次
  ``**/*.py`` 内容搜索要读几万个小文件；
- **任意深度 glob 语义**：fnmatch 对相对路径匹配（``*.png`` 命中任意深度，
  对齐 Claude Code Glob；fnmatch 的 ``*`` 天然跨 ``/``，``**`` 与之等价）；
- **二进制嗅探**：读前 8KB 查 NUL 字节（对齐 dsh fsio 的 BINARY_SAMPLE_BYTES）。
  read_file 的扩展名表覆盖不到无扩展名/冷门扩展名的二进制文件，且文本
  读取走 ``errors="replace"`` 永不抛解码异常——NUL 采样是唯一可靠防线。
"""

from __future__ import annotations

import fnmatch
import os
from typing import Iterator, List, NamedTuple, Optional, Pattern

# 默认剪枝目录：版本控制 / 依赖 / 构建产物 / 工具缓存
DEFAULT_EXCLUDE_DIRS = frozenset({
    ".git", ".svn", ".hg", ".bzr",
    "node_modules", "__pycache__", ".venv", "venv", "site-packages",
    "dist", "build", "target", ".tox", ".eggs",
    ".idea", ".vscode",
    ".pytest_cache", ".mypy_cache", ".ruff_cache",
})

# 二进制嗅探的采样字节数
BINARY_SAMPLE_BYTES = 8192

# 内容检索：跳过的已知二进制扩展名（纯性能优化，嗅探是正确性防线）
_CONTENT_SKIP_EXTS = frozenset({
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".ico",
    ".mp3", ".wav", ".ogg", ".flac", ".m4a", ".opus", ".amr",
    ".mp4", ".avi", ".mkv", ".mov", ".webm",
    ".zip", ".tar", ".gz", ".7z", ".rar", ".bz2", ".xz",
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".exe", ".dll", ".so", ".dylib", ".bin", ".dat", ".pyc", ".class",
    ".sqlite3", ".db", ".woff", ".woff2", ".ttf", ".otf",
})


class MatchEntry(NamedTuple):
    """一次匹配命中的条目（相对路径以 "/" 分隔，展示友好）。"""

    relpath: str
    abspath: str
    is_dir: bool
    size: int
    mtime: float


def resolve_exclude_dirs() -> frozenset:
    """读配置解析剪枝目录集合（search_exclude_dirs，逗号分隔；空值回退默认）。"""
    from core.config import get_config
    default = ",".join(sorted(DEFAULT_EXCLUDE_DIRS))
    raw = str(get_config("search_exclude_dirs", default) or "")
    names = frozenset(n.strip() for n in raw.split(",") if n.strip())
    return names or DEFAULT_EXCLUDE_DIRS


def iter_matches(
    root: str,
    pattern: str,
    exclude: Optional[frozenset] = None,
) -> Iterator[MatchEntry]:
    """按 glob 模式遍历匹配（剪枝噪声目录）。

    pattern 对相对路径 fnmatch：``*.png`` 命中任意深度文件（fnmatch 的
    ``*`` 天然跨 ``/``），``config/*.json`` 限定一级子目录。前导 ``**/``
    额外按文件名匹配余下模式——补齐 glob 递归模式的"零目录"语义
    （``**/*.py`` 也命中根级 a.py），与旧 glob.iglob(recursive=True) 对齐。
    目录同样参与匹配。
    """
    exclude = DEFAULT_EXCLUDE_DIRS if exclude is None else exclude
    # 前导 **/ 剥离后的备选模式（按 basename 匹配，覆盖零目录情形）
    alt = pattern
    while alt.startswith("**/"):
        alt = alt[3:]

    def _matched(rel: str, name: str) -> bool:
        if fnmatch.fnmatch(rel, pattern):
            return True
        return alt != pattern and fnmatch.fnmatch(name, alt)

    for dirpath, dirnames, filenames in os.walk(root):
        kept = [d for d in dirnames if d not in exclude]
        dirnames[:] = kept
        base = os.path.relpath(dirpath, root)
        for name in kept:
            rel = name if base == "." else f"{base}/{name}"
            if _matched(rel, name):
                abspath = os.path.join(dirpath, name)
                try:
                    stat = os.stat(abspath)
                    yield MatchEntry(rel, abspath, True, stat.st_size, stat.st_mtime)
                except OSError:
                    continue
        for name in filenames:
            rel = name if base == "." else f"{base}/{name}"
            if _matched(rel, name):
                abspath = os.path.join(dirpath, name)
                try:
                    stat = os.stat(abspath)
                except OSError:
                    continue
                yield MatchEntry(rel, abspath, False, stat.st_size, stat.st_mtime)


def looks_binary(path: str, sample_bytes: int = BINARY_SAMPLE_BYTES) -> bool:
    """内容级二进制判定：前 N 字节含 NUL 即视为二进制。

    UTF-8 文本合法包含 NUL 的场景不存在（UTF-8 用多字节编码 U+0000 之外的
    控制字符），采样命中即可放心判定；读取失败按非二进制处理（交由调用方
    的常规错误路径）。
    """
    try:
        with open(path, "rb") as f:
            sample = f.read(sample_bytes)
    except OSError:
        return False
    return b"\x00" in sample


class ContentHit(NamedTuple):
    """内容检索的单文件命中。"""

    relpath: str
    abspath: str
    lines: List[str]  # 形如 "12:命中行预览"


def content_search(
    root: str,
    pattern: str,
    regex: Pattern[str],
    exclude: Optional[frozenset] = None,
    *,
    max_results: int = 50,
    per_file_hits: int = 5,
    line_preview_chars: int = 200,
    max_file_bytes: int = 2 * 1024 * 1024,
) -> List[ContentHit]:
    """文件名 glob 过滤后做逐行内容检索（跳过二进制与超大文件）。

    max_file_bytes：内容模式逐文件逐行扫描，>2MB 的打包产物/数据文件
    慢且命中无操作价值，直接跳过（文件名模式不受限）。
    """
    hits: List[ContentHit] = []
    for entry in iter_matches(root, pattern, exclude):
        if entry.is_dir or entry.size > max_file_bytes:
            continue
        if os.path.splitext(entry.abspath)[1].lower() in _CONTENT_SKIP_EXTS:
            continue
        try:
            with open(entry.abspath, "r", encoding="utf-8", errors="replace") as f:
                matched: List[str] = []
                for i, line in enumerate(f, 1):
                    if regex.search(line):
                        matched.append(f"{i}:{line.rstrip()[:line_preview_chars]}")
                        if len(matched) >= per_file_hits:
                            break
        except OSError:
            continue
        if matched:
            hits.append(ContentHit(entry.relpath, entry.abspath, matched))
            if len(hits) >= max_results:
                break
    return hits


# ------------------------------------------------------------------
# 配置注册
# ------------------------------------------------------------------

_SCAN_CONFIGS = {
    "tools/search": {
        "search_exclude_dirs": {
            "description": "search_files 跳过的目录名（逗号分隔，不进结果也不再向下遍历）",
            "default": ",".join(sorted(DEFAULT_EXCLUDE_DIRS)),
            "advanced": True,
        },
    },
}

from core.config import register_configs_safe  # noqa: E402

register_configs_safe(_SCAN_CONFIGS)
