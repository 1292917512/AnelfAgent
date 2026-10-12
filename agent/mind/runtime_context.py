"""当前执行会话的后台任务摘要，只追加到逐轮动态尾部。"""

from __future__ import annotations

import time

from agent.mind.background_tasks import BackgroundTaskRegistry, current_background_task_id
from core.sanitizer import sanitize_text
from core.tags import tag_label


def background_context(registry: BackgroundTaskRegistry | None, scope: str) -> str:
    """动态尾部提供当前会话进度、停滞时长和可取消 ID，不消费通知、不改变缓存前缀。"""
    if registry is None or not scope:
        return ""
    tasks = registry.running(scope)
    recent = [result for result in registry.completed(scope)
              if result.kind == "llm_hook" and time.time() - result.finished_at < 300][-3:]
    if not tasks and not recent:
        return ""
    lines = [f"[后台任务] 当前会话 {len(tasks)} 项运行中；受理不代表完成，勿重复启动。"]
    own_id = current_background_task_id.get()
    tasks.sort(key=lambda task: task.task_id != own_id)
    for task in tasks[:8]:
        labels = tag_label("task_id", task.task_id) + tag_label("kind", task.kind)
        description = " ".join(sanitize_text(task.description).split())[:160]
        progress = registry.progress(task.task_id)
        phase = str(progress.get("phase", "running"))
        labels += tag_label("status", phase)
        labels += tag_label("notification", task.notification.value)
        if task.task_id == own_id:
            labels += tag_label("current_task", "true")
        operation = " ".join(sanitize_text(str(progress.get("operation", ""))).split())[:120]
        idle = max(0, int(time.time() - float(progress.get("updated_at", task.started_at))))
        lines.append(f"{labels} 已耗时 {max(0, int(task.elapsed))} 秒，距最近进展 {idle} 秒：{description}"
                     + (f"；当前 {operation}" if operation else ""))
    lines.append("耗时/暂无进展仅为事实，不代表失败。check_background_tasks(task_id=...) 查看详情；确认无需继续时 terminate_background_task。当前任务用 end_reply 收束。")
    if len(tasks) > 8:
        lines.append(f"另有 {len(tasks) - 8} 项；check_background_tasks 可查询完整状态。")
    for result in recent:
        summary = " ".join(sanitize_text(result.summary).split())[:180]
        lines.append(tag_label("task_id", result.task_id) + tag_label("kind", result.kind)
                     + tag_label("status", "completed" if result.success else "failed") + " " + summary)
    return "\n".join(lines)
