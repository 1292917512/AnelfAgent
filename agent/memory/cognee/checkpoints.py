"""关闭数据库后的索引快照；仅回退 Cognee 派生数据，绝不回退原生记忆库。

SDK 始终使用原目录，快照永不交给数据库引擎打开，因而无需改写持久化文档路径。
进程中断后先恢复，再启动 SDK；恢复中再次中断也按同一快照重做。
"""

from __future__ import annotations

import ctypes
import hashlib
import importlib.metadata
import json
import os
import shutil
import stat
import sys
import uuid
from pathlib import Path
from typing import Any

from filelock import FileLock

from core.file_utils import atomic_write_text
from core.log import log

PARTS = ("system", "data", "anelf-embedding.json")
CONTROL = ".publication"


def runtime_versions() -> dict[str, str]:
    """版本用于审计与升级验收，不把普通依赖升级等同于投影重建。"""
    versions = {}
    for name in ("cognee", "litellm", "ladybug", "lancedb", "pylance"):
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = ""
    return versions


def _sync_directory(path: Path) -> None:
    if os.name != "nt":
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def _clone(source: Path, target: Path) -> bool:
    """优先文件系统块共享；硬链接会让写入污染恢复点，禁止作为替代。"""
    if sys.platform == "darwin":
        library = ctypes.CDLL("/usr/lib/libSystem.B.dylib", use_errno=True)
        clonefile = library.clonefile
        clonefile.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_int]
        clonefile.restype = ctypes.c_int
        return clonefile(os.fsencode(source), os.fsencode(target), 0) == 0
    if sys.platform.startswith("linux"):
        import fcntl

        try:
            with source.open("rb") as src, target.open("xb") as dst:
                fcntl.ioctl(dst.fileno(), 0x40049409, src.fileno())  # FICLONE
            return True
        except OSError:
            target.unlink(missing_ok=True)
    return False


class IndexCheckpoints:
    """单宿主拥有快照目录；文件操作还与工作进程的独占锁互斥。"""

    def __init__(self, root: Path, *, copy_limit_bytes: int = 2 * 1024**3) -> None:
        self.root = root.resolve()
        self.control = self.root / CONTROL
        self.copy_limit_bytes = copy_limit_bytes
        self.state: dict[str, Any] = {}
        self._safe(self.control)

    def _save(self, state: dict[str, Any] | None = None) -> None:
        atomic_write_text(self.control / "state.json", json.dumps(state or self.state, ensure_ascii=False))
        _sync_directory(self.control)

    def _safe(self, path: Path) -> Path:
        reparse = path.exists() and bool(getattr(path.lstat(), "st_file_attributes", 0)
                                        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
        if reparse or path.is_symlink() or not path.resolve().is_relative_to(self.root):
            raise RuntimeError(f"索引保护目录含越界路径: {path.name}")
        return path

    def _remove(self, path: Path) -> None:
        self._safe(path)
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink(missing_ok=True)

    def _copy(self, source: Path, destination: Path) -> dict[str, int]:
        stats = {"cloned_bytes": 0, "copied_bytes": 0}

        def copy_file(src: str, dst: str) -> str:
            original, target = Path(src), Path(dst)
            self._safe(original)
            self._safe(target)
            size = original.stat().st_size
            if _clone(original, target):
                stats["cloned_bytes"] += size
            else:
                if stats["copied_bytes"] + size > self.copy_limit_bytes:
                    raise RuntimeError("文件系统不支持块共享，索引快照超过完整复制预算；原恢复点保留")
                if shutil.disk_usage(self.root).free < size + 256 * 1024**2:
                    raise RuntimeError("磁盘剩余空间不足以安全建立索引恢复点")
                shutil.copy2(original, target)
                stats["copied_bytes"] += size
            with target.open("r+b") as stream:
                os.fsync(stream.fileno())
            return dst

        destination.mkdir(parents=True, exist_ok=True)
        for name in PARTS:
            item = self._safe(source / name)
            if item.is_dir():
                for directory, directories, files in os.walk(item):
                    for child in (*directories, *files):
                        path = Path(directory) / child
                        if path.is_symlink():
                            raise RuntimeError("索引树含符号链接，不能建立隔离快照")
                shutil.copytree(item, destination / name, copy_function=copy_file)
            elif item.exists():
                copy_file(str(item), str(destination / name))
        # 目录项先持久化，再发布引用它们的提交记录。
        for directory, _, _ in os.walk(destination, topdown=False):
            _sync_directory(Path(directory))
        return stats

    def load(self) -> None:
        self.control.mkdir(parents=True, exist_ok=True)
        path = self.control / "state.json"
        if path.exists():
            self.state = json.loads(path.read_text(encoding="utf-8"))
            name = self.state.get("committed", "")
            if (self.state.get("version") != 1 or not isinstance(name, str)
                    or len(name) != 32 or any(c not in "0123456789abcdef" for c in name)
                    or not (self.control / name).is_dir()):
                raise RuntimeError("索引提交记录无效，停止自动恢复；原数据保留")
        else:
            self.state = {"version": 1, "dirty": False, "committed": "", "commits": 0}
            self.commit()
        self._read_proof(verify_files=True)
        if self.state.get("dirty"):
            self.restore()
        with FileLock(str(self.control / "worker.lock"), timeout=0):
            self._discard_unreferenced()

    def _discard_unreferenced(self) -> None:
        """回收发布前崩溃遗留的未引用快照，不遍历用户备份目录。"""
        for path in self.control.iterdir():
            managed = len(path.name) == 32 and all(c in "0123456789abcdef" for c in path.name)
            if (managed and path.name != self.state["committed"]) or path.name == "restoring":
                try:
                    self._remove(path)
                except OSError as exc:
                    log(f"Cognee 未引用保护快照清理延后: {type(exc).__name__}", "WARNING")

    def mark_open(self) -> None:
        self.state["dirty"] = True
        self._save()

    def _read_proof(self, *, verify_files: bool = False) -> dict[str, Any]:
        digest = self.state.get("proof_sha256")
        if not digest:
            return {}  # 已发布的旧恢复点在下一次正常提交时补齐，不强制重建。
        root = self.control / self.state["committed"]
        payload = self._safe(root / "proof.json").read_bytes()
        if hashlib.sha256(payload).hexdigest() != digest:
            raise RuntimeError("索引恢复点清单校验失败，停止恢复；工作区和原生内容保留")
        proof: dict[str, Any] = json.loads(payload)
        if proof.get("version") != 1 or not isinstance(proof.get("files"), dict):
            raise RuntimeError("索引恢复点清单格式不受支持")
        if verify_files:
            for name, size in proof["files"].items():
                file = self._safe(root / name)
                if not file.resolve().is_relative_to(root.resolve()) or not file.is_file() or file.stat().st_size != size:
                    raise RuntimeError(f"索引恢复点文件缺失或大小变化: {name}；禁止覆盖工作区")
        return proof

    def read_manifest(self) -> dict[str, Any] | None:
        return self._read_proof().get("index")

    def commit(self, manifest: dict[str, Any] | None = None) -> None:
        """调用者必须已关闭 SDK 并等待子进程退出；先完整快照，再原子发布。"""
        with FileLock(str(self.control / "worker.lock"), timeout=0):
            name = uuid.uuid4().hex
            target = self.control / name
            try:
                stats = self._copy(self.root, target)
                files = {path.relative_to(target).as_posix(): path.stat().st_size
                         for path in target.rglob("*") if path.is_file()}
                proof = json.dumps({"version": 1, "files": files, "index": manifest,
                                    "runtime": runtime_versions()}, ensure_ascii=False, sort_keys=True)
                atomic_write_text(target / "proof.json", proof)
                _sync_directory(target)
            except BaseException:
                self._remove(target)
                raise
            previous = self.state.get("committed")
            next_state = {**self.state, "committed": name, "dirty": False,
                          "commits": int(self.state.get("commits", 0)) + 1,
                          "proof_sha256": hashlib.sha256(proof.encode("utf-8")).hexdigest(), **stats}
            self._save(next_state)
            self.state = next_state
            # 仅删除自身产生的过期快照；用户备份和旧数据集不在这个目录。
            if previous:
                try:
                    self._remove(self.control / previous)
                except OSError as exc:
                    log(f"Cognee 旧保护快照清理延后: {type(exc).__name__}", "WARNING")

    def restore(self) -> None:
        """恢复可重入；dirty 只在全部组件完成替换后清除。"""
        with FileLock(str(self.control / "worker.lock"), timeout=5):
            self._read_proof(verify_files=True)
            source = self.control / self.state["committed"]
            staging = self.control / "restoring"
            self._remove(staging)
            self._copy(source, staging)
            for name in PARTS:
                target = self.root / name
                self._remove(target)
                if (staging / name).exists():
                    os.replace(staging / name, target)
            _sync_directory(self.root)
            self.state["dirty"] = False
            self._save()
            self._remove(staging)
