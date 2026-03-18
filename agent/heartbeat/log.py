"""心跳日志 — 记录心跳周期摘要，写入 heartbeat.md。

纯文件 I/O，无状态，不依赖 Mind 实例。
"""

from __future__ import annotations

import time as _time
from pathlib import Path
from typing import List, Optional

from core.log import log

LOG_PATH = Path("config/memory/heartbeat.md")


def _max_entries() -> int:
    try:
        from agent.config import get_config_provider
        return get_config_provider().mind.heartbeat_max_entries
    except Exception:
        return 50


def load_recent(count: int = 3) -> str:
    """加载最近 N 条心跳日志块。"""
    if not LOG_PATH.exists():
        return ""
    try:
        text = LOG_PATH.read_text("utf-8")
        blocks = [b for b in text.split("\n### ") if b.strip()]
        recent = blocks[-count:] if len(blocks) > count else blocks
        return "\n### ".join(recent)
    except Exception:
        return ""


def append_entry(text: str) -> None:
    """向最后一条心跳日志追加一行内容。"""
    try:
        if LOG_PATH.exists():
            content = LOG_PATH.read_text("utf-8")
            LOG_PATH.write_text(content.rstrip() + f"\n- {text}\n", encoding="utf-8")
    except Exception as e:
        log(f"心跳日志追加失败: {e}", "DEBUG")


def write_log(
    task_names: Optional[List[str]] = None,
    exec_results: Optional[List[str]] = None,
    *,
    pending_messages: int = 0,
    active_goals: int = 0,
) -> None:
    """追加心跳日志条目，自动裁剪超出上限的旧记录。"""
    ts = _time.strftime("%Y-%m-%d %H:%M:%S")
    lines = [f"### {ts} 心跳"]
    lines.append(f"- 态势：{pending_messages} 条消息, {active_goals} 个活跃目标")
    if task_names:
        lines.append(f"- 执行任务：{', '.join(task_names)}")
    if exec_results:
        lines.append(f"- 结果：{'; '.join(exec_results)}")
    lines.append("")
    entry = "\n".join(lines)

    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        existing = LOG_PATH.read_text("utf-8") if LOG_PATH.exists() else ""
        text = (
            existing.rstrip() + "\n\n" + entry
            if existing.strip()
            else f"# 心跳日志\n\n{entry}"
        )

        blocks = text.split("\n### ")
        max_n = _max_entries()
        if len(blocks) > max_n + 1:
            text = blocks[0] + "\n### " + "\n### ".join(blocks[-max_n:])

        LOG_PATH.write_text(text, encoding="utf-8")
    except Exception as e:
        log(f"心跳日志写入失败: {e}", "DEBUG")
