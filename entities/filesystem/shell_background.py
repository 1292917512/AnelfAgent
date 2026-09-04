"""后台 shell 执行 — run_shell_command 的 run_in_background 实现。

对齐 Claude Code Bash 后台语义：
- 立即返回任务 ID 与输出文件路径，不阻塞当前轮
- stdout/stderr 合并写入 .tool-results/ 输出文件（可用 read_file 随时查看）
- 完成时经 BackgroundTaskRegistry 通知（轮内会合 / 轮外注入，复用委派同款机制）
- 超时是提醒不是击杀：超过预期时长仍运行时向 AI 报告进度（elapsed + 输出
  尾部），去留由 AI 决策——系统为 AI 服务，终止权在 AI（terminate_background_
  task，经注册表 killer 句柄整组击杀进程组）

线程模型：同步工具经 asyncio.to_thread 执行（无事件循环），
因此用 Popen + threading 等待线程；完成/提醒通知由注册表的
call_soon_threadsafe 桥回到主循环（见 background_tasks.bind_loop）。

Model Experience：启动返回 message / 超时提醒 / 完成 summary 均为工具结果
或一次性历史通知（尾部动态区与水位线后追加），不触碰前缀缓存层。
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from typing import Any, Dict, Optional

from core.config import get_config_int
from core.log import log
from core.tool_errors import error_from_exception
from entities._sdk import get_background_registry, get_owner_scope

# 完成通知摘要的最大长度（输出文件尾部摘录）
_SUMMARY_TAIL_CHARS = 2000
# 超时提醒携带的输出尾部长度
_ALERT_TAIL_CHARS = 800


def launch_background(command: str, cwd: str, workspace: str,
                      description: str = "", timeout_sec: float = 0.0) -> Dict[str, Any]:
    """启动后台 shell 任务。

    Args:
        timeout_sec: 预期时长（秒，0 = 读 background_shell_alert_after 配置，
            配置为 0 则不提醒）；超过后向 AI 发送超时提醒并附最新输出，
            不终止进程——终止由 AI 经 terminate_background_task 决策。

    Returns:
        {ok, background, task_id, output_file, message} 或 {error}
    """
    registry = get_background_registry()
    scope = get_owner_scope()

    out_dir = os.path.join(os.path.abspath(workspace), ".tool-results")
    os.makedirs(out_dir, exist_ok=True)
    output_file = os.path.join(out_dir, f"shell-bg-{int(time.time())}-{os.getpid()}.log")

    popen_kwargs: Dict[str, Any] = {}
    if os.name != "nt":
        # 独立进程组：AI 终止时整组击杀，防 shell 孙进程泄漏（对齐前台 run_command）
        popen_kwargs["start_new_session"] = True
    # 环境变量卫生：NO_COLOR/pager/locale 等（用户环境值优先，见 shell_env）
    from core.shell_env import shell_env_defaults
    env = {**shell_env_defaults(), **os.environ}
    try:
        out_fp = open(output_file, "w", encoding="utf-8", errors="replace")
        proc = subprocess.Popen(
            command,
            shell=True,
            stdout=out_fp,
            stderr=subprocess.STDOUT,
            cwd=cwd,
            env=env,
            text=True,
            **popen_kwargs,
        )
    except Exception as exc:
        out_fp.close()
        return json.loads(error_from_exception(exc, action="启动后台任务"))

    if timeout_sec <= 0:
        timeout_sec = float(max(0, get_config_int("background_shell_alert_after", 1800)))

    desc = description or command[:60]
    if registry is not None:
        task_id = registry.register(scope, "shell", desc, expected_seconds=timeout_sec)
        # 关联输出文件：check_background_tasks(task_id=...) 即可增量消费输出，
        # 轮询长任务不再全量重读日志文件（单游标增量语义）
        registry.attach_output_file(task_id, output_file)
        # 终止句柄：AI 决策终止时整组击杀，终态由等待线程照常登记
        killed = threading.Event()

        def _kill() -> bool:
            from core.command import terminate_process_group
            killed.set()
            terminate_process_group(proc)
            return True

        registry.attach_killer(task_id, _kill)
    else:
        # 注册表不可用（如独立测试）：退化为仅输出文件跟踪
        task_id = f"local-{proc.pid}"
        killed = threading.Event()

    thread = threading.Thread(
        target=_wait_and_complete,
        args=(proc, out_fp, output_file, task_id, registry, timeout_sec, killed),
        name=f"shell-bg-{task_id}",
        daemon=True,
    )
    thread.start()
    log(f"后台 shell 任务已启动: {task_id} (pid={proc.pid}, 预期={timeout_sec or '不限'}) {desc}", tag="后台")

    expect_note = (
        f"预计 {timeout_sec:.0f}s 内完成，超时会收到一份进度报告"
        "（不会自动终止，去留由你判断）；"
        if timeout_sec > 0 else ""
    )
    return {
        "ok": True,
        "background": True,
        "task_id": task_id,
        "pid": proc.pid,
        "output_file": output_file,
        "timeout_seconds": int(timeout_sec) if timeout_sec > 0 else 0,
        "message": "命令已在后台执行。完成后系统会自动通知你；"
                   f"{expect_note}"
                   "期间用 check_background_tasks(task_id=...) 可增量查看新输出"
                   "（只返回新增，不重复）；不再需要时 terminate_background_task"
                   "(task_id=...) 随时可用。",
    }


def _wait_and_complete(proc: subprocess.Popen, out_fp, output_file: str,
                       task_id: str, registry: Optional[Any],
                       expected_sec: float, killed: threading.Event) -> None:
    """等待进程结束并通知注册表（等待线程，经注册表桥回主循环）。"""
    started_at = time.time()
    try:
        returncode = proc.wait(timeout=expected_sec if expected_sec > 0 else None)
    except subprocess.TimeoutExpired:
        # 超时是提醒不是击杀：报告进度事实（由 AI 甄别真假超时），继续等待自然结束
        detail = (
            f"已运行 {time.time() - started_at:.0f}s（预期 {expected_sec:.0f}s），"
            f"还在勤勤恳恳地跑。最近输出：\n"
            f"{_tail(output_file, _ALERT_TAIL_CHARS) or '（暂无输出）'}"
        )
        if registry is not None:
            registry.alert_timeout(task_id, detail)
        log(f"后台 shell 任务超时提醒: {task_id} (预期 {expected_sec:.0f}s)", "WARNING", tag="后台")
        returncode = proc.wait()
    out_fp.close()
    summary = _tail(output_file, _SUMMARY_TAIL_CHARS)
    if killed.is_set():
        head = f"已被 AI 终止（退出码 {returncode}）"
        success = False
    else:
        head = f"退出码 {returncode}"
        success = returncode == 0
    full_summary = f"{head}\n{summary}" if summary else head
    if registry is not None:
        registry.complete(task_id, success, full_summary)
    log(f"后台 shell 任务结束: {task_id} ({head})", tag="后台")


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


# ------------------------------------------------------------------
# 配置注册
# ------------------------------------------------------------------

_BG_SHELL_CONFIGS = {
    "entity/os": {
        "background_shell_alert_after": {
            "description": "后台 shell 任务的预期时长（秒）：run_shell_command 未显式传 "
                           "timeout 时生效，超过后向 AI 发送进度报告（附最新输出；不终止"
                           "进程，是确实需要更久还是卡住、要不要终止均由 AI 判断）；"
                           "0 = 关闭报告",
            "default": 1800,
            "advanced": True,
            "unit": "秒",
        },
    },
}

from core.config import register_configs_safe  # noqa: E402

register_configs_safe(_BG_SHELL_CONFIGS)
