"""单实例所有权与安全停止：操作系统文件锁防双写，存活实例不被自动强杀。"""

from __future__ import annotations

import os
import time
from pathlib import Path

import psutil
from filelock import FileLock, Timeout

from core.log import log

_locks: dict[Path, FileLock] = {}


def _pid_alive(pid: int) -> bool:
    try:
        return psutil.Process(pid).status() != psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return False
    except psutil.AccessDenied:
        return True


def _read_pid(path: Path) -> int:
    try:
        raw = path.read_text(encoding="utf-8").strip()
        return int(raw) if raw.isdecimal() else 0
    except OSError:
        return 0


def _is_our_instance(process: psutil.Process, project_root: Path, script: str = "launch.py") -> bool:
    """结合工作目录解析脚本参数，支持相对路径，避免 PID 复用误操作。"""
    try:
        cwd = Path(process.cwd())
        expected = (project_root / script).resolve()
        return any((cwd / arg).resolve() == expected for arg in process.cmdline()
                   if Path(arg).name == script)
    except (psutil.Error, OSError):
        return False


def acquire_instance(pid_file: Path, project_root: Path) -> None:
    """独占当前检出的实例锁；旧实例仍存活时拒绝启动并保持其 PID 文件。"""
    path = pid_file.resolve()
    if path in _locks:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = FileLock(str(path) + ".lock", timeout=0)
    try:
        lock.acquire()
    except Timeout as exc:
        raise RuntimeError("已有实例正在运行或安全关停，请等待退出后再启动") from exc
    try:
        pid = _read_pid(path)
        if pid and pid != os.getpid() and _pid_alive(pid):
            # 同时保护升级前尚未使用文件锁的实例；无法核实身份也不覆盖所有权。
            raise RuntimeError(f"PID {pid} 仍存活，拒绝接管；请先安全停止旧实例")
        path.write_text(str(os.getpid()), encoding="utf-8")
        _locks[path] = lock
    except BaseException:
        lock.release()
        raise


def release_instance(pid_file: Path) -> None:
    """全部关键资源关闭后释放所有权；锁文件本身保留，避免 inode 竞争。"""
    path = pid_file.resolve()
    try:
        if _read_pid(path) == os.getpid():
            path.unlink(missing_ok=True)
    except OSError as exc:
        log(f"PID 文件清理失败: {exc}", "WARNING", tag="实例")
    finally:
        lock = _locks.pop(path, None)
        if lock is not None:
            lock.release()


def stop_instance(project_root: Path, timeout: float = 600.0) -> None:
    """POSIX 下核实归属后通知守护与宿主退出，共用等待预算；超时不强杀。"""
    if os.name == "nt":
        raise RuntimeError("Windows 请通过应用关停入口或启动终端 Ctrl+C 安全停止")
    targets: list[psutil.Process] = []
    for filename, script in (("anelf_guardian.pid", "start.sh"), ("anelf.pid", "launch.py")):
        path = project_root / "logs" / filename
        pid = _read_pid(path)
        if not pid or not _pid_alive(pid):
            continue
        try:
            process = psutil.Process(pid)
            if not _is_our_instance(process, project_root, script):
                raise RuntimeError(f"无法确认 PID {pid} 的项目归属，停止操作未执行")
            targets.append(process)
        except psutil.NoSuchProcess:
            pass
    # 先通知守护，再通知应用；守护可能正等待前台应用退出，不能先等守护退出。
    for process in targets:
        try:
            process.terminate()
        except psutil.NoSuchProcess:
            pass
    deadline = time.monotonic() + timeout
    for process in reversed(targets):
        announced = time.monotonic()
        try:
            while process.is_running() and process.status() != psutil.STATUS_ZOMBIE:
                if time.monotonic() >= deadline:
                    raise RuntimeError(f"PID {process.pid} 仍在安全关停；未强杀、未启动新实例，请检查日志")
                if time.monotonic() - announced >= 5:
                    print(f"⏳ 等待 PID {process.pid} 退出；具体清理阶段见 logs/startup.log", flush=True)
                    announced = time.monotonic()
                time.sleep(0.2)
        except psutil.NoSuchProcess:
            pass  # 信号与检查间退出属正常关停。


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="安全停止 AnelfAgent，不强杀数据库进程")
    parser.add_argument("--timeout", type=float, default=600.0)
    args = parser.parse_args()
    try:
        stop_instance(Path(__file__).resolve().parent.parent, max(0.0, args.timeout))
    except (RuntimeError, psutil.Error) as error:
        parser.exit(1, f"{error}\n")
