"""当前执行会话的后台任务摘要，只追加到逐轮动态尾部。"""

from __future__ import annotations

from agent.mind.background_tasks import BackgroundTaskRegistry
from core.sanitizer import sanitize_text
from core.tags import tag_label


def background_context(registry: BackgroundTaskRegistry | None, scope: str) -> str:
    """只读当前会话运行事实，不消费完成通知或读取任务输出。"""
    if registry is None or not scope:
        return ""
    tasks = registry.running(scope)
    if not tasks:
        return ""
    lines = [f"[后台任务] 当前会话 {len(tasks)} 项运行中；受理不代表完成，勿重复启动。"]
    for task in tasks[:8]:
        labels = tag_label("task_id", task.task_id) + tag_label("kind", task.kind)
        description = " ".join(sanitize_text(task.description).split())[:160]
        lines.append(f"{labels} 运行中，已耗时 {max(0, int(task.elapsed))} 秒：{description}")
    if len(tasks) > 8:
        lines.append(f"另有 {len(tasks) - 8} 项；check_background_tasks 可查询完整状态。")
    return "\n".join(lines)
