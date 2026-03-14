"""控制台管理工具。

提供跨平台的控制台进程管理功能，支持实时输出监听和命令注入。
"""

import os
import platform
import subprocess
import threading
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

from core.async_helper import dual_mode
from core.log import log


class ConsoleState(Enum):
    IDLE = "idle"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"
    ERROR = "error"


@dataclass
class ConsoleOutput:
    """控制台输出数据"""
    type: str  # stdout / stderr
    content: str
    timestamp: float


@dataclass
class ConsoleStatus:
    """控制台状态信息"""
    state: ConsoleState
    pid: Optional[int] = None
    return_code: Optional[int] = None


EventCallback = Callable[[Any], None]


class ConsoleManager:
    """控制台管理器，支持跨平台进程管理与事件回调。

    事件名：
        state_changed  — ConsoleState 变化（data: ConsoleState）
        started        — 进程启动成功（data: dict with pid/command）
        stopped        — 进程退出（data: dict with return_code）
        output         — 标准输出/错误行（data: ConsoleOutput）
        command_sent   — 命令已写入 stdin（data: str）
        error          — 启动失败（data: str）
    """

    def __init__(self, encoding: Optional[str] = None) -> None:
        self._process: Optional[subprocess.Popen] = None
        self._state = ConsoleState.IDLE
        self._encoding = encoding or ("gbk" if platform.system() == "Windows" else "utf-8")
        self._threads: List[threading.Thread] = []
        self._lock = threading.Lock()
        self._listeners: Dict[str, List[EventCallback]] = {}

    # ------------------------------------------------------------------
    # 事件订阅
    # ------------------------------------------------------------------

    def on(self, event_name: str, callback: EventCallback) -> None:
        """订阅事件。"""
        self._listeners.setdefault(event_name, []).append(callback)

    def off(self, event_name: str, callback: EventCallback) -> None:
        """取消订阅事件。"""
        callbacks = self._listeners.get(event_name, [])
        if callback in callbacks:
            callbacks.remove(callback)

    def _emit(self, event_name: str, data: Any = None) -> None:
        for cb in list(self._listeners.get(event_name, [])):
            try:
                cb(data)
            except Exception as exc:
                log(f"控制台事件回调异常: event={event_name} {exc}", "ERROR")

    # ------------------------------------------------------------------
    # 进程控制
    # ------------------------------------------------------------------

    @dual_mode
    def start(
        self,
        command: Union[str, List[str]],
        cwd: Optional[str] = None,
        env: Optional[dict] = None,
        shell: Optional[bool] = None,
    ) -> bool:
        """启动控制台进程。"""
        with self._lock:
            if self._state == ConsoleState.RUNNING:
                log("控制台已在运行中", "WARNING")
                return False

            try:
                self._state = ConsoleState.STARTING
                self._emit("state_changed", ConsoleState.STARTING)

                cmd_str = command if isinstance(command, str) else " ".join(command)
                log(f"启动控制台: {cmd_str[:100]}{'...' if len(cmd_str) > 100 else ''}", "INFO")

                process_env = os.environ.copy()
                if env:
                    process_env.update(env)

                use_shell = isinstance(command, str) if shell is None else shell

                startup_info = None
                if platform.system() == "Windows":
                    startup_info = subprocess.STARTUPINFO()
                    startup_info.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                    startup_info.wShowWindow = subprocess.SW_HIDE

                self._process = subprocess.Popen(
                    command,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    shell=use_shell,
                    cwd=cwd,
                    env=process_env,
                    startupinfo=startup_info,
                    bufsize=1,
                    universal_newlines=True,
                    encoding=self._encoding,
                    errors="replace",
                )

                self._start_output_threads()
                self._state = ConsoleState.RUNNING
                self._emit("state_changed", ConsoleState.RUNNING)
                self._emit("started", {"pid": self._process.pid, "command": cmd_str})

                log(f"控制台启动成功，PID: {self._process.pid}", "INFO")
                return True

            except Exception as exc:
                self._state = ConsoleState.ERROR
                error_msg = f"启动失败: {exc}"
                log(f"控制台{error_msg}", "ERROR")
                self._emit("error", error_msg)
                return False

    @dual_mode
    def send_command(self, command: str, add_newline: bool = True) -> bool:
        """发送命令到控制台标准输入。"""
        with self._lock:
            if self._state != ConsoleState.RUNNING or not self._process:
                log("控制台未运行，无法发送命令", "WARNING")
                return False

            try:
                cmd_to_send = command if not add_newline or command.endswith("\n") else command + "\n"
                self._process.stdin.write(cmd_to_send)
                self._process.stdin.flush()
                self._emit("command_sent", command)
                return True
            except Exception as exc:
                log(f"发送命令失败: {exc}", "ERROR")
                return False

    @dual_mode
    def stop(self, timeout: float = 5.0) -> bool:
        """停止控制台进程。"""
        with self._lock:
            if self._state != ConsoleState.RUNNING or not self._process:
                log("控制台未运行", "WARNING")
                return True

            try:
                self._state = ConsoleState.STOPPING
                self._emit("state_changed", ConsoleState.STOPPING)
                log("正在停止控制台...", "INFO")

                self._process.terminate()

                deadline = time.monotonic() + timeout
                while self._process.poll() is None and time.monotonic() < deadline:
                    time.sleep(0.1)

                if self._process.poll() is None:
                    log("进程未响应，强制终止", "WARNING")
                    self._process.kill()
                    self._process.wait()

                self._state = ConsoleState.STOPPED
                self._emit("state_changed", ConsoleState.STOPPED)
                log("控制台已停止", "INFO")
                return True

            except Exception as exc:
                log(f"停止控制台失败: {exc}", "ERROR")
                self._state = ConsoleState.ERROR
                return False

    # ------------------------------------------------------------------
    # 状态查询
    # ------------------------------------------------------------------

    def get_status(self) -> ConsoleStatus:
        with self._lock:
            status = ConsoleStatus(state=self._state)
            if self._process:
                status.pid = self._process.pid
                status.return_code = self._process.poll()
            return status

    def is_running(self) -> bool:
        return self._state == ConsoleState.RUNNING

    # ------------------------------------------------------------------
    # 内部线程
    # ------------------------------------------------------------------

    def _start_output_threads(self) -> None:
        for pipe, output_type in (
            (self._process.stdout, "stdout"),
            (self._process.stderr, "stderr"),
        ):
            t = threading.Thread(
                target=self._read_output, args=(pipe, output_type), daemon=True
            )
            t.start()
            self._threads.append(t)

        monitor = threading.Thread(target=self._monitor_process, daemon=True)
        monitor.start()
        self._threads.append(monitor)

    def _read_output(self, pipe: Any, output_type: str) -> None:
        try:
            while True:
                if not pipe or pipe.closed:
                    break
                line = pipe.readline()
                if not line:
                    break
                line = line.rstrip("\r\n")
                if line:
                    self._emit(
                        "output",
                        ConsoleOutput(
                            type=output_type,
                            content=line,
                            timestamp=time.time(),
                        ),
                    )
        except Exception as exc:
            if self._state == ConsoleState.RUNNING:
                log(f"读取 {output_type} 失败: {exc}", "ERROR")

    def _monitor_process(self) -> None:
        try:
            return_code = self._process.wait()
            with self._lock:
                if self._state != ConsoleState.STOPPING:
                    self._state = ConsoleState.STOPPED
            self._emit("stopped", {"return_code": return_code})
            log(f"控制台进程结束，返回码: {return_code}", "INFO")
        except Exception as exc:
            log(f"监控进程失败: {exc}", "ERROR")


# ------------------------------------------------------------------
# 便捷函数
# ------------------------------------------------------------------

@dual_mode
def create_console(encoding: Optional[str] = None) -> ConsoleManager:
    """创建控制台管理器实例。"""
    return ConsoleManager(encoding=encoding)


@dual_mode
def run_interactive_command(
    command: Union[str, List[str]],
    on_output: Optional[Callable[[str], None]] = None,
    timeout: float = 60.0,
) -> Tuple[bool, str]:
    """运行交互式命令并收集输出。"""
    console = ConsoleManager()
    outputs: List[str] = []

    def handle_output(data: ConsoleOutput) -> None:
        outputs.append(data.content)
        if on_output:
            on_output(data.content)

    console.on("output", handle_output)

    if not console.start(command):
        return False, "启动命令失败"

    start_time = time.monotonic()
    while console.is_running() and time.monotonic() - start_time < timeout:
        time.sleep(0.1)

    if console.is_running():
        console.stop()
        return False, f"命令执行超时 ({timeout}s)"

    status = console.get_status()
    output_text = "\n".join(outputs)

    if status.return_code == 0:
        return True, output_text
    return False, f"命令执行失败 (返回码: {status.return_code})\n{output_text}"
