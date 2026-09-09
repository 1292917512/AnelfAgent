"""单实例守卫：PID 文件 + 启动清场，杜绝残留实例占用端口。

问题背景：launch.py 可被启动任意多次（手动启动 / 历史脚本 / 守护循环），
实例之间互不认识；残留实例持续占用 WebUI 端口，新实例 bind 失败后静默运行，
用户访问到的永远是老代码（2026-09 实证：8/29 残留进程占位 10 天）。

机制：
- 启动时写 PID 文件（项目 logs/ 目录，天然按检出副本隔离实例身份）
- 若 PID 文件指向的活进程 cmdline 属于本项目 launch.py → 判定为残留实例，
  SIGTERM 优雅终止 → 宽限期后 SIGKILL 强杀，随后接管
- cmdline 不匹配（PID 复用/外来进程）→ 只警告绝不误杀
- 关停时释放 PID 文件（仅当文件指向自己）
"""

from __future__ import annotations

import os
import signal
import time
from pathlib import Path
from typing import Optional

from core.log import log

# SIGTERM 后的优雅退出宽限（秒），超时升级 SIGKILL
_TERM_GRACE_SECONDS = 10.0
_TERM_POLL_INTERVAL = 0.2

_TAG = "实例"


def _pid_alive(pid: int) -> bool:
    """进程是否存活（僵尸进程视为已死：不再持有任何资源）。"""
    try:
        import psutil

        return psutil.Process(pid).status() != psutil.STATUS_ZOMBIE
    except Exception as exc:
        # psutil 拿不到（竞态退出/权限）时退回 kill(pid, 0) 探测
        import psutil as _psutil  # noqa: F401
        if isinstance(exc, _psutil.NoSuchProcess):
            return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # 活着但属于其他用户
    except OSError:
        return False


def _is_our_instance(pid: int, project_root: Path) -> bool:
    """校验目标进程 cmdline 属于本项目的 launch.py（防 PID 复用误杀）。"""
    try:
        import psutil

        cmdline = " ".join(psutil.Process(pid).cmdline())
    except Exception as exc:
        log(f"读取进程 {pid} 命令行失败: {exc}", "DEBUG", tag=_TAG)
        return False
    return "launch.py" in cmdline and str(project_root) in cmdline


def _terminate(pid: int) -> bool:
    """SIGTERM → 宽限 → SIGKILL；返回进程是否已退出。"""
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        return not _pid_alive(pid)
    deadline = time.monotonic() + _TERM_GRACE_SECONDS
    while time.monotonic() < deadline:
        if not _pid_alive(pid):
            return True
        time.sleep(_TERM_POLL_INTERVAL)
    try:
        os.kill(pid, signal.SIGKILL)
    except OSError:
        pass
    time.sleep(0.3)
    return not _pid_alive(pid)


def acquire_instance(pid_file: Path, project_root: Path) -> Optional[int]:
    """获取实例所有权：清场残留实例并写入自身 PID。

    Returns:
        被清理的残留实例 PID；无残留返回 None。
    """
    killed: Optional[int] = None
    try:
        raw = pid_file.read_text(encoding="utf-8").strip() if pid_file.exists() else ""
        old_pid = int(raw) if raw.isdigit() else 0
    except OSError:
        old_pid = 0
    if old_pid and old_pid != os.getpid() and _pid_alive(old_pid):
        if _is_our_instance(old_pid, project_root):
            log(f"检测到残留实例（PID {old_pid}），正在清场以接管端口...", "WARNING", tag=_TAG)
            if _terminate(old_pid):
                killed = old_pid
                log(f"残留实例已终止（PID {old_pid}）", tag=_TAG)
            else:
                log(f"残留实例（PID {old_pid}）无法终止，端口可能仍被占用", "ERROR", tag=_TAG)
        else:
            log(
                f"PID 文件指向非本项目进程（PID {old_pid}），忽略（不误杀）",
                "WARNING", tag=_TAG,
            )
    try:
        pid_file.parent.mkdir(parents=True, exist_ok=True)
        pid_file.write_text(str(os.getpid()), encoding="utf-8")
    except OSError as exc:
        log(f"PID 文件写入失败（实例守卫失效）: {exc}", "WARNING", tag=_TAG)
    return killed


def release_instance(pid_file: Path) -> None:
    """释放实例所有权（仅当 PID 文件指向当前进程）。"""
    try:
        if pid_file.exists() and pid_file.read_text(encoding="utf-8").strip() == str(os.getpid()):
            pid_file.unlink()
    except OSError as exc:
        log(f"PID 文件清理失败（已忽略）: {exc}", "DEBUG", tag=_TAG)
