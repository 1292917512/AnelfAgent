"""工作区文件 API 路由 — 浏览、读取、编辑、搜索 workspace/ 目录。

所有路径经 entities.filesystem 的沙箱检查，限制在 workspace_root 内。
前端工作台文件树/编辑器通过本模块访问工作区。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from core.log import log

router = APIRouter(prefix="/workspace", tags=["workspace"])

_MAX_READ_BYTES = 512 * 1024
_MAX_WRITE_BYTES = 2 * 1024 * 1024
_TREE_MAX_ENTRIES = 500
_SEARCH_MAX_RESULTS = 30
_SEARCHABLE_EXTS = {
    ".py", ".js", ".ts", ".tsx", ".jsx", ".json", ".md", ".txt", ".yaml", ".yml",
    ".toml", ".cfg", ".ini", ".html", ".css", ".xml", ".sh", ".sql", ".csv",
}
_SKIP_DIRS = {"__pycache__", ".git", "node_modules", ".venv", "venv"}

_TEXT_EXTS = _SEARCHABLE_EXTS | {
    ".log", ".env", ".gitignore", ".dockerfile", ".conf", ".prompt",
}


def _workspace_root() -> str:
    """返回工作区根目录绝对路径。"""
    try:
        from core.config import ConfigManager
        root = ConfigManager.get("workspace_root", "workspace")
    except Exception:
        root = "workspace"
    root_abs = os.path.abspath(root)
    os.makedirs(root_abs, exist_ok=True)
    return root_abs


def _safe_path(path: str) -> str:
    """委托 entities.filesystem 的沙箱路径解析。"""
    from entities.filesystem.tools import _safe_path as fs_safe_path
    try:
        return fs_safe_path(path)
    except ValueError as e:
        raise HTTPException(status_code=403, detail=str(e)) from e


def _rel(path: str) -> str:
    """绝对路径转工作区相对路径（posix 风格）。"""
    return os.path.relpath(path, _workspace_root()).replace(os.sep, "/")


def _is_binary(path: str) -> bool:
    """通过扩展名与内容采样判断是否为二进制文件。"""
    ext = Path(path).suffix.lower()
    if ext in _TEXT_EXTS:
        return False
    try:
        with open(path, "rb") as f:
            chunk = f.read(1024)
        return b"\x00" in chunk
    except OSError:
        return True


def _entry(abs_path: str, *, with_children: bool, depth: int, budget: List[int]) -> Optional[Dict[str, Any]]:
    """构建单个目录树条目，budget[0] 为剩余条目配额。"""
    if budget[0] <= 0:
        return None
    name = os.path.basename(abs_path)
    try:
        st = os.stat(abs_path)
    except OSError:
        return None
    is_dir = os.path.isdir(abs_path)
    if is_dir and name in _SKIP_DIRS:
        return None
    budget[0] -= 1
    node: Dict[str, Any] = {
        "name": name,
        "path": _rel(abs_path),
        "type": "dir" if is_dir else "file",
        "modified": int(st.st_mtime),
    }
    if is_dir:
        node["children"] = _list_dir(abs_path, depth=depth, budget=budget) if with_children and depth > 0 else []
    else:
        node["size"] = st.st_size
        node["binary"] = _is_binary(abs_path)
    return node


def _list_dir(dir_abs: str, *, depth: int, budget: List[int]) -> List[Dict[str, Any]]:
    """列出一层目录（文件夹优先，按名称排序），按需递归。"""
    try:
        names = sorted(os.listdir(dir_abs), key=lambda n: (not os.path.isdir(os.path.join(dir_abs, n)), n.lower()))
    except OSError:
        return []
    nodes: List[Dict[str, Any]] = []
    for name in names:
        if name.startswith("."):
            continue
        node = _entry(os.path.join(dir_abs, name), with_children=True, depth=depth, budget=budget)
        if node is None:
            continue
        nodes.append(node)
        if budget[0] <= 0:
            break
    return nodes


@router.get("/tree")
async def get_tree(path: str = Query(""), depth: int = Query(2, ge=1, le=6)) -> Dict[str, Any]:
    """获取工作区目录树（默认两层，懒加载可传子路径）。"""
    base = _safe_path(path) if path else _workspace_root()
    if not os.path.isdir(base):
        raise HTTPException(status_code=404, detail="目录不存在")
    budget = [_TREE_MAX_ENTRIES]
    children = _list_dir(base, depth=depth, budget=budget)
    return {
        "path": "" if base == _workspace_root() else _rel(base),
        "children": children,
        "truncated": budget[0] <= 0,
    }


@router.get("/file")
async def read_file(path: str = Query(...)) -> Dict[str, Any]:
    """读取文本文件内容（上限 512KB，二进制文件只返回元信息）。"""
    fp = _safe_path(path)
    if not os.path.isfile(fp):
        raise HTTPException(status_code=404, detail="文件不存在")
    size = os.path.getsize(fp)
    result: Dict[str, Any] = {
        "path": _rel(fp),
        "name": os.path.basename(fp),
        "size": size,
        "modified": int(os.path.getmtime(fp)),
        "binary": _is_binary(fp),
        "truncated": False,
        "content": "",
    }
    if result["binary"] or size > _MAX_READ_BYTES:
        if not result["binary"]:
            result["truncated"] = True
        return result
    try:
        result["content"] = Path(fp).read_text("utf-8", errors="replace")
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"读取失败: {e}") from e
    return result


@router.get("/raw")
async def serve_raw_file(path: str = Query(...)) -> Any:
    """以原始字节服务工作区文件（图片/音视频预览用），按扩展名推断 Content-Type。"""
    from starlette.responses import FileResponse
    fp = _safe_path(path)
    if not os.path.isfile(fp):
        raise HTTPException(status_code=404, detail="文件不存在")
    return FileResponse(fp, filename=os.path.basename(fp))


class FileWriteRequest(BaseModel):
    path: str
    content: str


@router.put("/file")
async def write_file(req: FileWriteRequest) -> Dict[str, Any]:
    """写入（新建或覆盖）文本文件。"""
    if len(req.content.encode("utf-8")) > _MAX_WRITE_BYTES:
        raise HTTPException(status_code=413, detail="文件内容超过 2MB 限制")
    fp = _safe_path(req.path)
    if os.path.isdir(fp):
        raise HTTPException(status_code=400, detail="目标是目录")
    try:
        os.makedirs(os.path.dirname(fp), exist_ok=True)
        Path(fp).write_text(req.content, encoding="utf-8")
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"写入失败: {e}") from e
    log(f"工作台写入文件: {_rel(fp)}", "DEBUG", tag="工作区")
    return {"status": "ok", "path": _rel(fp), "size": os.path.getsize(fp)}


class MkdirRequest(BaseModel):
    path: str


@router.post("/mkdir")
async def make_dir(req: MkdirRequest) -> Dict[str, Any]:
    fp = _safe_path(req.path)
    try:
        os.makedirs(fp, exist_ok=True)
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"创建目录失败: {e}") from e
    return {"status": "ok", "path": _rel(fp)}


@router.delete("/file")
async def delete_file(path: str = Query(...)) -> Dict[str, str]:
    fp = _safe_path(path)
    if not os.path.exists(fp):
        raise HTTPException(status_code=404, detail="路径不存在")
    try:
        if os.path.isdir(fp):
            import shutil
            shutil.rmtree(fp)
        else:
            os.remove(fp)
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"删除失败: {e}") from e
    log(f"工作台删除: {_rel(fp)}", "DEBUG", tag="工作区")
    return {"status": "ok"}


@router.get("/search")
async def search_files(q: str = Query(..., min_length=1), limit: int = Query(_SEARCH_MAX_RESULTS, ge=1, le=100)) -> Dict[str, Any]:
    """搜索工作区：文件名匹配 + 文本内容匹配。"""
    root = _workspace_root()
    query = q.lower()
    name_hits: List[Dict[str, Any]] = []
    content_hits: List[Dict[str, Any]] = []

    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS and not d.startswith(".")]
        for fname in filenames:
            if fname.startswith("."):
                continue
            fp = os.path.join(dirpath, fname)
            rel = _rel(fp)
            if query in fname.lower():
                name_hits.append({"path": rel, "name": fname, "match": "name"})
                if len(name_hits) >= limit:
                    break
            ext = Path(fname).suffix.lower()
            if ext not in _SEARCHABLE_EXTS or len(content_hits) >= limit:
                continue
            try:
                if os.path.getsize(fp) > _MAX_READ_BYTES:
                    continue
                text = Path(fp).read_text("utf-8", errors="ignore")
            except OSError:
                continue
            idx = text.lower().find(query)
            if idx >= 0:
                start = max(0, idx - 40)
                snippet = text[start:idx + len(q) + 60].replace("\n", " ")
                content_hits.append({"path": rel, "name": fname, "match": "content", "snippet": snippet})
        if len(name_hits) >= limit and len(content_hits) >= limit:
            break

    return {"query": q, "files": (name_hits + content_hits)[:limit]}


def search_workspace(q: str, limit: int = 10) -> List[Dict[str, Any]]:
    """供全局搜索聚合复用的同步版本。"""
    root = _workspace_root()
    query = q.lower()
    hits: List[Dict[str, Any]] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS and not d.startswith(".")]
        for fname in filenames:
            if fname.startswith(".") or len(hits) >= limit:
                continue
            fp = os.path.join(dirpath, fname)
            rel = _rel(fp)
            if query in fname.lower():
                hits.append({"path": rel, "name": fname, "match": "name"})
                continue
            if Path(fname).suffix.lower() not in _SEARCHABLE_EXTS:
                continue
            try:
                if os.path.getsize(fp) > _MAX_READ_BYTES:
                    continue
                text = Path(fp).read_text("utf-8", errors="ignore")
            except OSError:
                continue
            idx = text.lower().find(query)
            if idx >= 0:
                start = max(0, idx - 40)
                snippet = text[start:idx + len(q) + 60].replace("\n", " ")
                hits.append({"path": rel, "name": fname, "match": "content", "snippet": snippet})
        if len(hits) >= limit:
            break
    return hits
