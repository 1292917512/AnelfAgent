"""工作区文件 API 路由 — 浏览、读取、编辑、搜索 workspace/ 目录。

所有路径经 entities.filesystem 的沙箱检查，限制在 workspace_root 内。
前端工作台文件树/编辑器通过本模块访问工作区。
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from pydantic import BaseModel

from core.log import log
from web.routers._errors import server_error
from web.routers._paths import safe_workspace_path

router = APIRouter(prefix="/workspace", tags=["workspace"])

_MAX_READ_BYTES = 512 * 1024
_MAX_WRITE_BYTES = 2 * 1024 * 1024
_MAX_UPLOAD_BYTES = 50 * 1024 * 1024
_TREE_MAX_ENTRIES = 500
_PROJECT_TREE_MAX_ENTRIES = 3000
# 单层目录返回上限：顶层（请求目标本身）不受全局配额限制，只受本上限约束，
# 保证任何目录都能被逐层展开到（修复配额被浅层兄弟耗尽导致深层分支消失的问题）
_DIR_MAX_CHILDREN = 500
_SEARCH_MAX_RESULTS = 30
_SEARCHABLE_EXTS = {
    ".py", ".js", ".ts", ".tsx", ".jsx", ".json", ".md", ".txt", ".yaml", ".yml",
    ".toml", ".cfg", ".ini", ".html", ".css", ".xml", ".sh", ".sql", ".csv",
}
_SKIP_DIRS = {"__pycache__", ".git", "node_modules", ".venv", "venv", "dist", "build"}

_TEXT_EXTS = _SEARCHABLE_EXTS | {
    ".log", ".env", ".gitignore", ".dockerfile", ".conf", ".prompt",
}


def _workspace_root() -> str:
    """返回工作区根目录绝对路径（统一解析入口，与 AI 文件工具同基准）。"""
    from services.filesystem import workspace_root
    root_abs = workspace_root()
    os.makedirs(root_abs, exist_ok=True)
    return root_abs


def _project_root() -> str:
    """返回项目根目录绝对路径（launch.py / pyproject.toml / .git 所在处）。"""
    from core.path import project_root
    return project_root()


def _resolve_root(root: str) -> str:
    """按 root 参数解析基准目录，仅支持 workspace / project。"""
    if root == "project":
        return _project_root()
    return _workspace_root()


def _safe_path(path: str) -> str:
    """委托 entities.filesystem 的沙箱路径解析。"""
    try:
        return safe_workspace_path(path)
    except ValueError as e:
        raise HTTPException(status_code=403, detail=str(e)) from e


def _safe_project_path(path: str) -> str:
    """项目根沙箱路径解析（限制在项目根内，符号链接解析后校验）。"""
    root = os.path.realpath(_project_root())
    abs_path = os.path.realpath(os.path.join(root, path)) if path else root
    if abs_path != root and not abs_path.startswith(root + os.sep):
        raise HTTPException(status_code=403, detail="路径超出项目根目录")
    return abs_path


def _resolve(path: str, root: str) -> str:
    """按 root 统一解析路径：workspace 走工作区沙箱，project 限制在项目根内。"""
    if root == "project":
        return _safe_project_path(path)
    return _safe_path(path) if path else _workspace_root()


def _rel(path: str, *, root: str = "") -> str:
    """绝对路径转相对路径（posix 风格）。"""
    base = root or _workspace_root()
    return os.path.relpath(path, base).replace(os.sep, "/")


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


def _dir_has_visible_children(dir_abs: str) -> bool:
    """目录是否含有可见子项（与树渲染同一过滤口径：隐藏项 / _SKIP_DIRS 不算）。"""
    try:
        with os.scandir(dir_abs) as it:
            for e in it:
                if e.name.startswith("."):
                    continue
                if e.is_dir(follow_symlinks=False) and e.name in _SKIP_DIRS:
                    continue
                return True
    except OSError:
        pass
    return False


def _entry(
    abs_path: str,
    *,
    depth: int,
    budget: List[int],
    stats: Dict[str, bool],
    root: str = "",
    free: bool = False,
) -> Optional[Dict[str, Any]]:
    """构建单个目录树条目。

    budget[0] 为剩余全局配额（仅递归展开的层级消耗）；free=True 的层（请求目标
    的直接子级）不消耗配额，保证逐层懒加载永远拿得到完整的一层。stats["truncated"]
    记录本次请求是否发生过任何截断（配额耗尽或单层超限）。
    """
    if not free:
        if budget[0] <= 0:
            stats["truncated"] = True
            return None
        budget[0] -= 1
    name = os.path.basename(abs_path)
    try:
        st = os.stat(abs_path)
    except OSError:
        return None
    is_dir = os.path.isdir(abs_path)
    if is_dir and name in _SKIP_DIRS:
        return None
    node: Dict[str, Any] = {
        "name": name,
        "path": _rel(abs_path, root=root),
        "type": "dir" if is_dir else "file",
        "modified": int(st.st_mtime),
    }
    if is_dir:
        node["has_children"] = _dir_has_visible_children(abs_path)
        if depth > 0 and node["has_children"]:
            node["children"] = _list_dir(abs_path, depth=depth - 1, budget=budget, stats=stats, root=root)
    else:
        node["size"] = st.st_size
        node["binary"] = _is_binary(abs_path)
    return node


def _list_dir(
    dir_abs: str,
    *,
    depth: int,
    budget: List[int],
    stats: Dict[str, bool],
    root: str = "",
    free: bool = False,
) -> List[Dict[str, Any]]:
    """列出一层目录（文件夹优先，按名称排序），按需递归。free 语义同 _entry。"""
    try:
        names = sorted(os.listdir(dir_abs), key=lambda n: (not os.path.isdir(os.path.join(dir_abs, n)), n.lower()))
    except OSError:
        return []
    nodes: List[Dict[str, Any]] = []
    for name in names:
        if name.startswith("."):
            continue
        if len(nodes) >= _DIR_MAX_CHILDREN:
            stats["truncated"] = True
            break
        node = _entry(os.path.join(dir_abs, name), depth=depth, budget=budget, stats=stats, root=root, free=free)
        if node is None:
            continue
        nodes.append(node)
    return nodes


@router.get("/tree")
async def get_tree(
    path: str = Query(""),
    depth: int = Query(2, ge=1, le=6),
    root: str = Query("workspace"),
) -> Dict[str, Any]:
    """获取目录树（默认两层，懒加载可传子路径）。

    root=project 时浏览整个项目根目录，规则与工作区完全一致（仅基准目录不同）。
    请求目标的直接子级不受全局配额限制（仅受单层 500 条上限），全局配额只约束
    递归预取的更深层级——任何目录都能逐层展开，不会因浅层兄弟过多而消失。
    """
    base_root = _resolve_root(root)
    base = _resolve(path, root)
    if not os.path.isdir(base):
        raise HTTPException(status_code=404, detail="目录不存在")
    budget = [_PROJECT_TREE_MAX_ENTRIES if root == "project" else _TREE_MAX_ENTRIES]
    stats: Dict[str, bool] = {"truncated": False}
    # 目录遍历为同步磁盘 I/O（项目根 3000 条配额），移入线程避免阻塞事件循环
    children = await asyncio.to_thread(_list_dir, base, depth=depth, budget=budget, stats=stats, root=base_root, free=True)
    return {
        "path": "" if base == base_root else _rel(base, root=base_root),
        "children": children,
        "truncated": stats["truncated"],
    }


@router.get("/file")
async def read_file(path: str = Query(...), root: str = Query("workspace")) -> Dict[str, Any]:
    """读取文本文件内容（上限 512KB，二进制文件只返回元信息）。"""
    fp = _resolve(path, root)
    if not os.path.isfile(fp):
        raise HTTPException(status_code=404, detail="文件不存在")
    size = os.path.getsize(fp)
    result: Dict[str, Any] = {
        "path": _rel(fp, root=_resolve_root(root)),
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
        result["content"] = await asyncio.to_thread(Path(fp).read_text, "utf-8", errors="replace")
    except OSError as e:
        raise server_error("读取文件", e) from e
    return result


@router.get("/raw")
async def serve_raw_file(path: str = Query(...), inline: bool = False, root: str = Query("workspace")) -> Any:
    """以原始字节服务文件（图片/音视频/PDF 预览用），按扩展名推断 Content-Type。

    inline=True 时以 inline 方式返回（供 iframe 内联渲染），默认 attachment（下载语义）。
    """
    from starlette.responses import FileResponse
    fp = _resolve(path, root)
    if not os.path.isfile(fp):
        raise HTTPException(status_code=404, detail="文件不存在")
    return FileResponse(
        fp,
        filename=os.path.basename(fp),
        content_disposition_type="inline" if inline else "attachment",
    )


class FileWriteRequest(BaseModel):
    path: str
    content: str
    root: str = "workspace"


@router.put("/file")
async def write_file(req: FileWriteRequest) -> Dict[str, Any]:
    """写入（新建或覆盖）文本文件。"""
    if len(req.content.encode("utf-8")) > _MAX_WRITE_BYTES:
        raise HTTPException(status_code=413, detail="文件内容超过 2MB 限制")
    fp = _resolve(req.path, req.root)
    if os.path.isdir(fp):
        raise HTTPException(status_code=400, detail="目标是目录")
    try:
        await asyncio.to_thread(_write_text_file, fp, req.content)
    except OSError as e:
        raise server_error("写入文件", e) from e
    log(f"工作台写入文件: {_rel(fp, root=_resolve_root(req.root))}", "DEBUG", tag="工作区")
    return {"status": "ok", "path": _rel(fp, root=_resolve_root(req.root)), "size": os.path.getsize(fp)}


def _write_text_file(fp: str, content: str) -> None:
    """创建父目录并写入文本文件（同步实现，供 to_thread 调用）。"""
    os.makedirs(os.path.dirname(fp), exist_ok=True)
    Path(fp).write_text(content, encoding="utf-8")


class MkdirRequest(BaseModel):
    path: str
    root: str = "workspace"


@router.post("/mkdir")
async def make_dir(req: MkdirRequest) -> Dict[str, Any]:
    fp = _resolve(req.path, req.root)
    try:
        os.makedirs(fp, exist_ok=True)
    except OSError as e:
        raise server_error("创建目录", e) from e
    return {"status": "ok", "path": _rel(fp, root=_resolve_root(req.root))}


class MoveRequest(BaseModel):
    src: str
    dst: str
    root: str = "workspace"


@router.post("/move")
async def move_entry(req: MoveRequest) -> Dict[str, Any]:
    """重命名 / 移动文件或目录（dst 为目标全路径，目标父目录须已存在）。"""
    base_root = _resolve_root(req.root)
    src = _resolve(req.src, req.root)
    dst = _resolve(req.dst, req.root)
    if not os.path.exists(src):
        raise HTTPException(status_code=404, detail="源路径不存在")
    if src == dst:
        return {"status": "ok", "path": _rel(src, root=base_root)}
    if dst == base_root:
        raise HTTPException(status_code=400, detail="不能覆盖根目录")
    if os.path.exists(dst):
        raise HTTPException(status_code=409, detail="目标路径已存在")
    if os.path.isdir(src):
        src_real = os.path.realpath(src)
        dst_real = os.path.realpath(dst)
        if dst_real == src_real or dst_real.startswith(src_real + os.sep):
            raise HTTPException(status_code=400, detail="不能把目录移动到自身内部")
    if not os.path.isdir(os.path.dirname(dst)):
        raise HTTPException(status_code=404, detail="目标目录不存在")
    try:
        import shutil
        await asyncio.to_thread(shutil.move, src, dst)
    except OSError as e:
        raise server_error("移动", e) from e
    rel = _rel(dst, root=base_root)
    log(f"工作台移动: {_rel(src, root=base_root)} -> {rel}", "DEBUG", tag="工作区")
    return {"status": "ok", "path": rel}


@router.post("/upload")
async def upload_file(
    file: UploadFile = File(...),
    dir: str = Query(""),
    root: str = Query("workspace"),
) -> Dict[str, Any]:
    """上传文件到指定目录（multipart，上限 50MB，重名拒绝覆盖）。"""
    target_dir = _resolve(dir, root)
    if not os.path.isdir(target_dir):
        raise HTTPException(status_code=404, detail="目标目录不存在")
    name = os.path.basename(file.filename or "").strip()
    if not name or name.startswith(".") or name in _SKIP_DIRS:
        raise HTTPException(status_code=400, detail="非法文件名")
    fp = os.path.join(target_dir, name)
    if os.path.exists(fp):
        raise HTTPException(status_code=409, detail="同名文件已存在")
    try:
        await _save_upload(file, fp)
    except ValueError as e:
        raise HTTPException(status_code=413, detail=str(e)) from e
    except OSError as e:
        raise server_error("上传文件", e) from e
    rel = _rel(fp, root=_resolve_root(root))
    log(f"工作台上传: {rel} ({os.path.getsize(fp)}B)", "DEBUG", tag="工作区")
    return {"status": "ok", "path": rel, "size": os.path.getsize(fp)}


async def _save_upload(file: UploadFile, fp: str) -> None:
    """分块落盘上传内容，超限即清理并抛 ValueError。"""
    total = 0
    try:
        with open(fp, "wb") as out:
            while chunk := await file.read(1024 * 1024):
                total += len(chunk)
                if total > _MAX_UPLOAD_BYTES:
                    raise ValueError("文件超过 50MB 上传限制")
                await asyncio.to_thread(out.write, chunk)
    finally:
        await file.close()
        if total > _MAX_UPLOAD_BYTES:
            try:
                os.remove(fp)
            except OSError:
                pass


@router.delete("/file")
async def delete_file(path: str = Query(...), root: str = Query("workspace")) -> Dict[str, str]:
    fp = _resolve(path, root)
    if not os.path.exists(fp):
        raise HTTPException(status_code=404, detail="路径不存在")
    try:
        if os.path.isdir(fp):
            import shutil
            shutil.rmtree(fp)
        else:
            os.remove(fp)
    except OSError as e:
        raise server_error("删除", e) from e
    log(f"工作台删除: {_rel(fp, root=_resolve_root(root))}", "DEBUG", tag="工作区")
    return {"status": "ok"}


@router.get("/search")
async def search_files(q: str = Query(..., min_length=1), limit: int = Query(_SEARCH_MAX_RESULTS, ge=1, le=100)) -> Dict[str, Any]:
    """搜索工作区：文件名匹配 + 文本内容匹配。"""
    # os.walk + 逐文件读取为同步阻塞 I/O，移入线程避免卡住事件循环
    return await asyncio.to_thread(_search_files_impl, _workspace_root(), q, limit)


def _search_files_impl(root: str, q: str, limit: int) -> Dict[str, Any]:
    """在工作区内执行文件名 + 内容搜索（同步实现，供 to_thread 调用）。"""
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
    """供全局搜索聚合复用的同步版本（与 /search 端点同一实现）。"""
    return _search_files_impl(_workspace_root(), q, limit)["files"]
