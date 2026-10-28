"""隐藏伪终端适配：Windows ConPTY、POSIX PTY，不打开桌面终端窗口。"""

from __future__ import annotations

import codecs
import errno
import importlib
import importlib.util
import logging
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Protocol

from app.processes import background_options

ROWS, COLUMNS = 40, 120


class Terminal(Protocol):
    """非阻塞读取与显式回收由安装操作拥有的进程树。"""

    def read(self) -> str: ...
    def poll(self) -> int | None: ...
    def close(self) -> None: ...
    def terminate(self) -> None: ...


def available() -> bool:
    """源码 CLI 不要求额外依赖；缺失时使用普通流式回执。"""
    return importlib.util.find_spec("pyte") is not None and (
        os.name != "nt" or importlib.util.find_spec("winpty") is not None)


def spawn(args: list[str], cwd: Path, env: dict[str, str]) -> Terminal:
    """仅在创建进程前选择平台，不在失败后重复执行安装命令。"""
    terminal_env = {**env, "TERM": "xterm-256color", "COLUMNS": str(COLUMNS), "LINES": str(ROWS)}
    for key in ("UV_NO_PROGRESS", "NO_COLOR", "CI", "JPY_SESSION_NAME", "RUST_LOG"):
        terminal_env.pop(key, None)
    if sys.platform == "win32":
        return WindowsTerminal(args, cwd, terminal_env)
    else:
        return PosixTerminal(args, cwd, terminal_env)


class WindowsTerminal:
    """使用 pywinpty 的原生 ConPTY 后端，不创建 cmd 或可见控制台。"""

    def __init__(self, args: list[str], cwd: Path, env: dict[str, str]) -> None:
        winpty = importlib.import_module("winpty")
        self.pty = winpty.PTY(COLUMNS, ROWS, backend=winpty.Backend.ConPTY)
        command = subprocess.list2cmdline(args[1:])
        environment = "\0".join(f"{key}={value}" for key, value in sorted(env.items())) + "\0"
        if not self.pty.spawn(args[0], cmdline=" " + command, cwd=str(cwd), env=environment):
            raise RuntimeError("Unable to create the installation terminal")

    def read(self) -> str:
        try:
            chunks: list[str] = []
            size = 0
            deadline = time.monotonic() + .02
            while time.monotonic() < deadline:
                chunk = str(self.pty.read(blocking=False))
                if not chunk:
                    break
                chunks.append(chunk)
                size += len(chunk)
                if size >= 65536:
                    break
            return "".join(chunks)
        except Exception:
            if self.poll() is not None:
                return ""
            raise

    def poll(self) -> int | None:
        return None if self.pty.isalive() else int(self.pty.get_exitstatus())

    def terminate(self) -> None:
        if self.poll() is None:
            subprocess.run(["taskkill", "/PID", str(self.pty.pid), "/T", "/F"],
                           capture_output=True, timeout=15, **background_options())
            deadline = time.monotonic() + 5
            while self.poll() is None and time.monotonic() < deadline:
                time.sleep(.05)
            if self.poll() is None:
                raise RuntimeError("Installation process has not exited")

    def close(self) -> None:
        try:
            self.pty.cancel_io()
        except Exception as exc:
            logging.getLogger(__name__).debug("Terminal I/O already closed: %s", exc)
        finally:
            self.pty = None


if sys.platform != "win32":
    class PosixTerminal:
        """POSIX 文件描述符负责终端输出，会话组负责取消整个安装命令树。"""

        def __init__(self, args: list[str], cwd: Path, env: dict[str, str]) -> None:
            import pty
            import termios
            self.master, slave = pty.openpty()
            self.decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
            try:
                termios.tcsetwinsize(slave, (ROWS, COLUMNS))
                os.set_blocking(self.master, False)
                self.process = subprocess.Popen(args, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                                                stdout=slave, stderr=slave, start_new_session=True)
            except BaseException:
                os.close(self.master)
                raise
            finally:
                os.close(slave)

        def read(self) -> str:
            try:
                return self.decoder.decode(os.read(self.master, 65536))
            except BlockingIOError:
                return ""
            except OSError as exc:
                if exc.errno == errno.EIO:
                    return ""
                raise

        def poll(self) -> int | None:
            return self.process.poll()

        def terminate(self) -> None:
            if self.poll() is not None:
                return
            try:
                os.killpg(self.process.pid, signal.SIGTERM)
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(self.process.pid, signal.SIGKILL)
                self.process.wait(timeout=5)
            except ProcessLookupError:
                self.process.wait(timeout=5)

        def close(self) -> None:
            os.close(self.master)
