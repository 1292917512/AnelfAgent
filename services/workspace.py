"""工作区文件服务 — 目录树 / 读写 / 移动 / 上传 / 搜索的业务逻辑。

沙箱规则：workspace 根走 entities.filesystem 的沙箱解析（与 AI 文件工具同基准），
project 根限制在项目目录内（符号链接解析后校验）。错误经 WorkspaceError 携带
HTTP 语义（status_code），由路由层统一映射为响应。
"""

from __future__ import annotations

import asyncio
import os
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional, Protocol

from core.log import log
from core.path import project_root


class UploadStream(Protocol):
    """上传流接口（与框架无关，fastapi.UploadFile 天然满足）。"""

    async def read(self, n: int = -1) -> bytes: ...
    async def close(self) -> None: ...

MAX_READ_BYTES = 512 * 1024
_MAX_WRITE_BYTES = 2 * 1024 * 1024
_MAX_UPLOAD_BYTES = 50 * 1024 * 1024
_TREE_MAX_ENTRIES = 500
_PROJECT_TREE_MAX_ENTRIES = 3000
# 单层目录返回上限：顶层（请求目标本身）不受全局配额限制，只受本上限约束，
# 保证任何目录都能被逐层展开到
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


class WorkspaceError(Exception):
    """工作区操作错误（status_code/detail 由路由层映射为 HTTP 响应）。"""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


class WorkspaceService:
    """工作区文件操作（Web 侧入口，全部方法线程安全：同步 I/O 由调用方 to_thread）。"""

    # ------------------------------------------------------------------
    # 路径解析
    # ------------------------------------------------------------------

    @staticmethod
    def workspace_root() -> str:
        """返回工作区根目录绝对路径（统一解析入口，与 AI 文件工具同基准）。"""
        from services.filesystem import workspace_root
        root_abs = workspace_root()
        os.makedirs(root_abs, exist_ok=True)
        return root_abs

    def resolve_root(self, root: str) -> str:
        """按 root 参数解析基准目录，仅支持 workspace / project。"""
        if root == "project":
            return project_root()
        return self.workspace_root()

    def resolve(self, path: str, root: str) -> str:
        """按 root 统一解析路径：workspace 走工作区沙箱，project 限制在项目根内。"""
        if root == "project":
            return self._safe_project_path(path)
        if not path:
            return self.workspace_root()
        from services.filesystem import safe_workspace_path
        try:
            return safe_workspace_path(path)
        except ValueError as e:
            raise WorkspaceError(403, str(e)) from e

    @staticmethod
    def _safe_project_path(path: str) -> str:
        """项目根沙箱路径解析（限制在项目根内，符号链接解析后校验）。"""
        root = os.path.realpath(project_root())
        abs_path = os.path.realpath(os.path.join(root, path)) if path else root
        if abs_path != root and not abs_path.startswith(root + os.sep):
            raise WorkspaceError(403, "路径超出项目根目录")
        return abs_path

    def rel(self, path: str, *, root: str = "") -> str:
        """绝对路径转相对路径（posix 风格）。"""
        base = root or self.workspace_root()
        return os.path.relpath(path, base).replace(os.sep, "/")

    # ------------------------------------------------------------------
    # 目录树
    # ------------------------------------------------------------------

    def get_tree(self, path: str, depth: int, root: str) -> Dict[str, Any]:
        """获取目录树（请求目标的直接子级不受全局配额限制，仅受单层上限）。"""
        base_root = self.resolve_root(root)
        base = self.resolve(path, root)
        if not os.path.isdir(base):
            raise WorkspaceError(404, "目录不存在")
        budget = [_PROJECT_TREE_MAX_ENTRIES if root == "project" else _TREE_MAX_ENTRIES]
        stats: Dict[str, bool] = {"truncated": False}
        children = self._list_dir(base, depth=depth, budget=budget, stats=stats, root=base_root, free=True)
        return {
            "path": "" if base == base_root else self.rel(base, root=base_root),
            "children": children,
            "truncated": stats["truncated"],
        }

    def _list_dir(
        self,
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
            node = self._entry(os.path.join(dir_abs, name), depth=depth, budget=budget, stats=stats, root=root, free=free)
            if node is None:
                continue
            nodes.append(node)
        return nodes

    def _entry(
        self,
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
            "path": self.rel(abs_path, root=root),
            "type": "dir" if is_dir else "file",
            "modified": int(st.st_mtime),
        }
        if is_dir:
            node["has_children"] = _dir_has_visible_children(abs_path)
            if depth > 0 and node["has_children"]:
                node["children"] = self._list_dir(abs_path, depth=depth - 1, budget=budget, stats=stats, root=root)
        else:
            node["size"] = st.st_size
            node["binary"] = is_binary(abs_path)
        return node

    # ------------------------------------------------------------------
    # 文件读写
    # ------------------------------------------------------------------

    def read_file(self, path: str, root: str) -> Dict[str, Any]:
        """读取文本文件内容（上限 512KB，二进制文件只返回元信息）。"""
        fp = self.resolve(path, root)
        if not os.path.isfile(fp):
            raise WorkspaceError(404, "文件不存在")
        size = os.path.getsize(fp)
        result: Dict[str, Any] = {
            "path": self.rel(fp, root=self.resolve_root(root)),
            "name": os.path.basename(fp),
            "size": size,
            "modified": int(os.path.getmtime(fp)),
            "binary": is_binary(fp),
            "truncated": False,
            "content": "",
        }
        if result["binary"] or size > MAX_READ_BYTES:
            if not result["binary"]:
                result["truncated"] = True
            return result
        try:
            result["content"] = Path(fp).read_text("utf-8", errors="replace")
        except OSError as e:
            raise WorkspaceError(500, f"读取文件失败: {e}") from e
        return result

    def resolve_existing_file(self, path: str, root: str) -> str:
        """解析并校验一个存在的文件（原始字节服务用），返回绝对路径。"""
        fp = self.resolve(path, root)
        if not os.path.isfile(fp):
            raise WorkspaceError(404, "文件不存在")
        return fp

    def write_file(self, path: str, content: str, root: str) -> Dict[str, Any]:
        """写入（新建或覆盖）文本文件。"""
        if len(content.encode("utf-8")) > _MAX_WRITE_BYTES:
            raise WorkspaceError(413, "文件内容超过 2MB 限制")
        fp = self.resolve(path, root)
        if os.path.isdir(fp):
            raise WorkspaceError(400, "目标是目录")
        try:
            os.makedirs(os.path.dirname(fp), exist_ok=True)
            Path(fp).write_text(content, encoding="utf-8")
        except OSError as e:
            raise WorkspaceError(500, f"写入文件失败: {e}") from e
        rel = self.rel(fp, root=self.resolve_root(root))
        log(f"工作台写入文件: {rel}", "DEBUG", tag="工作区")
        return {"status": "ok", "path": rel, "size": os.path.getsize(fp)}

    def make_dir(self, path: str, root: str) -> Dict[str, Any]:
        """创建目录（已存在则幂等）。"""
        fp = self.resolve(path, root)
        try:
            os.makedirs(fp, exist_ok=True)
        except OSError as e:
            raise WorkspaceError(500, f"创建目录失败: {e}") from e
        return {"status": "ok", "path": self.rel(fp, root=self.resolve_root(root))}

    def move_entry(self, src: str, dst: str, root: str) -> Dict[str, Any]:
        """重命名 / 移动文件或目录（dst 为目标全路径，目标父目录须已存在）。"""
        base_root = self.resolve_root(root)
        src_abs = self.resolve(src, root)
        dst_abs = self.resolve(dst, root)
        if not os.path.exists(src_abs):
            raise WorkspaceError(404, "源路径不存在")
        if src_abs == dst_abs:
            return {"status": "ok", "path": self.rel(src_abs, root=base_root)}
        if dst_abs == base_root:
            raise WorkspaceError(400, "不能覆盖根目录")
        if os.path.exists(dst_abs):
            raise WorkspaceError(409, "目标路径已存在")
        if os.path.isdir(src_abs):
            src_real = os.path.realpath(src_abs)
            dst_real = os.path.realpath(dst_abs)
            if dst_real == src_real or dst_real.startswith(src_real + os.sep):
                raise WorkspaceError(400, "不能把目录移动到自身内部")
        if not os.path.isdir(os.path.dirname(dst_abs)):
            raise WorkspaceError(404, "目标目录不存在")
        try:
            shutil.move(src_abs, dst_abs)
        except OSError as e:
            raise WorkspaceError(500, f"移动失败: {e}") from e
        rel = self.rel(dst_abs, root=base_root)
        log(f"工作台移动: {self.rel(src_abs, root=base_root)} -> {rel}", "DEBUG", tag="工作区")
        return {"status": "ok", "path": rel}

    def delete_entry(self, path: str, root: str) -> None:
        """删除文件或目录（目录递归删除）。"""
        fp = self.resolve(path, root)
        if not os.path.exists(fp):
            raise WorkspaceError(404, "路径不存在")
        try:
            if os.path.isdir(fp):
                shutil.rmtree(fp)
            else:
                os.remove(fp)
        except OSError as e:
            raise WorkspaceError(500, f"删除失败: {e}") from e
        log(f"工作台删除: {self.rel(fp, root=self.resolve_root(root))}", "DEBUG", tag="工作区")

    def upload_target_dir(self, dir_path: str, root: str, filename: str) -> str:
        """校验上传目标目录与文件名，返回落盘绝对路径（重名拒绝覆盖）。"""
        target_dir = self.resolve(dir_path, root)
        if not os.path.isdir(target_dir):
            raise WorkspaceError(404, "目标目录不存在")
        name = os.path.basename(filename or "").strip()
        if not name or name.startswith(".") or name in _SKIP_DIRS:
            raise WorkspaceError(400, "非法文件名")
        fp = os.path.join(target_dir, name)
        if os.path.exists(fp):
            raise WorkspaceError(409, "同名文件已存在")
        return fp

    async def save_upload(self, file: UploadStream, fp: str, root: str) -> Dict[str, Any]:
        """分块落盘上传内容并返回结果（超限即清理并抛 WorkspaceError(413)）。"""
        total = 0
        try:
            with open(fp, "wb") as out:
                while chunk := await file.read(1024 * 1024):
                    total += len(chunk)
                    if total > _MAX_UPLOAD_BYTES:
                        raise WorkspaceError(413, "文件超过 50MB 上传限制")
                    await asyncio.to_thread(out.write, chunk)
        finally:
            await file.close()
            if total > _MAX_UPLOAD_BYTES:
                try:
                    os.remove(fp)
                except OSError:
                    pass
        rel = self.rel(fp, root=self.resolve_root(root))
        log(f"工作台上传: {rel} ({total}B)", "DEBUG", tag="工作区")
        return {"status": "ok", "path": rel, "size": total}

    # ------------------------------------------------------------------
    # 搜索
    # ------------------------------------------------------------------

    def search_files(self, q: str, limit: int = _SEARCH_MAX_RESULTS) -> Dict[str, Any]:
        """在工作区内执行文件名 + 内容搜索（同步实现，调用方负责 to_thread）。"""
        query = q.lower()
        root = self.workspace_root()
        name_hits: List[Dict[str, Any]] = []
        content_hits: List[Dict[str, Any]] = []

        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS and not d.startswith(".")]
            for fname in filenames:
                if fname.startswith("."):
                    continue
                fp = os.path.join(dirpath, fname)
                rel = self.rel(fp)
                if query in fname.lower():
                    name_hits.append({"path": rel, "name": fname, "match": "name"})
                    if len(name_hits) >= limit:
                        break
                ext = Path(fname).suffix.lower()
                if ext not in _SEARCHABLE_EXTS or len(content_hits) >= limit:
                    continue
                try:
                    if os.path.getsize(fp) > MAX_READ_BYTES:
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


def is_binary(path: str) -> bool:
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
