"""后台 shell 执行 — run_shell_command 的 run_in_background 实现。

对齐 Claude Code Bash 后台语义：
- 立即返回任务 ID 与输出文件路径，不阻塞当前轮
- stdout/stderr 合并写入 .tool-results/ 输出文件（可用 read_file 随时查看）
- 完成时经 BackgroundTaskRegistry 通知（轮内会合 / 轮外注入，复用委派同款机制）

线程模型：同步工具经 asyncio.to_thread 执行（无事件循环），
因此用 Popen + threading 等待线程；完成通知由注册表的
call_soon_threadsafe 桥回到主循环（见 background_tasks.bind_loop）。
"""

from __future__ import annotations

import os
import subprocess
import threading
import time
from typing import Any, Dict, Optional

from core.log import log
from entities._sdk import get_background_registry, get_current_scope

# 完成通知摘要的最大长度（输出文件尾部摘录）
_SUMMARY_TAIL_CHARS = 2000


def launch_background(command: str, cwd: str, workspace: str,
                      description: str = "") -> Dict[str, Any]:
    """启动后台 shell 任务。

    Returns:
        {ok, background, task_id, output_file, message} 或 {error}
    """
    registry = get_background_registry()
    scope = get_current_scope()

    out_dir = os.path.join(os.path.abspath(workspace), ".tool-results")
    os.makedirs(out_dir, exist_ok=True)
    output_file = os.path.join(out_dir, f"shell-bg-{int(time.time())}-{os.getpid()}.log")

    try:
        out_fp = open(output_file, "w", encoding="utf-8", errors="replace")
        proc = subprocess.Popen(
            command,
            shell=True,
            stdout=out_fp,
            stderr=subprocess.STDOUT,
            cwd=cwd,
            text=True,
        )
    except Exception as exc:
        out_fp.close()
        return {"error": f"后台任务启动失败: {exc}"}

    desc = description or command[:60]
    if registry is not None:
        task_id = registry.register(scope, "shell", desc)
    else:
        # 注册表不可用（如独立测试）：退化为仅输出文件跟踪
        task_id = f"local-{proc.pid}"

    thread = threading.Thread(
        target=_wait_and_complete,
        args=(proc, out_fp, output_file, task_id, registry),
        name=f"shell-bg-{task_id}",
        daemon=True,
    )
    thread.start()
    log(f"后台 shell 任务已启动: {task_id} (pid={proc.pid}) {desc}", tag="后台")

    return {
        "ok": True,
        "background": True,
        "task_id": task_id,
        "pid": proc.pid,
        "output_file": output_file,
        "message": "命令已在后台执行。完成后系统会自动通知你；"
                   "期间可用 read_file 查看输出文件进度，或用 check_background_tasks 查询状态。",
    }


def _wait_and_complete(proc: subprocess.Popen, out_fp, output_file: str,
                       task_id: str, registry: Optional[Any]) -> None:
    """等待进程结束并通知注册表（等待线程，经注册表桥回主循环）。"""
    returncode = proc.wait()
    out_fp.close()
    summary = _tail(output_file, _SUMMARY_TAIL_CHARS)
    head = f"退出码 {returncode}"
    full_summary = f"{head}\n{summary}" if summary else head
    if registry is not None:
        registry.complete(task_id, returncode == 0, full_summary)
    log(f"后台 shell 任务结束: {task_id} (退出码 {returncode})", tag="后台")


def _tail(path: str, max_chars: int) -> str:
    """读取文件尾部摘录。"""
    try:
        size = os.path.getsize(path)
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            if size > max_chars:
                f.seek(size - max_chars)
            return f.read().strip()
    except OSError:
        return ""
