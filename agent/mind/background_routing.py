"""后台任务完成与超时提醒的会话路由。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.async_helper import spawn

if TYPE_CHECKING:
    from agent.mind.mind import Mind


async def on_task_unclaimed(
    mind: Mind, scope: str, description: str, summary: str, success: bool = True,
) -> None:
    """后台任务轮外完成回调：完成事实写对话历史并排入回复队列触发新 REPLY。

    通知经 enqueue_scope_reply 落目标会话的对话历史（一次性事实固化，
    不驻留短期记忆——驻留会每轮催促已处理完的事项且反复打断缓存前缀）。
    非 conversation scope（心跳任务等无会话上下文发起的任务）无处写历史，
    退化为全局短期记忆桶（对齐 PushHub 全局兜底），任何下一周期可见。

    唤醒预算：成功与失败各自独立计数（上限同为 background_wake_budget，
    默认 3，0=关闭）——成功完成被抑制是良性的（信息在历史里，下次真人
    消息可见）；失败完成必须先耗尽独立额度才可被抑制，防"任务死了无人
    告知"（2026-08 监控脚本超时静默事故）。连续自动唤醒超预算时不触发
    新周期（防"完成→回复→又启后台任务→完成"的自我激励循环），完成事实
    已在历史中，下次真人消息或心跳周期触发时模型可见，信息不丢。
    """
    from agent.mind.tools.scheduler import enqueue_scope_reply
    header = "[后台任务失败]" if not success else "[后台任务完成]"
    prompt = (
        f"{header} {description}\n"
        f"结果：{summary[:800]}\n"
    )
    if not success:
        prompt += (
            "任务以失败收场——失败原因见上方结果；"
            "是否告知用户（send_message）、是否重试，由你判断。\n"
        )
    prompt += "请根据结果继续处理（回复用户请调用 send_message，完成请调用 end_reply）。"

    if not scope.startswith(("user_", "group_")):
        mind.pfc.add_temporary({"role": "system", "content": prompt})
        return
    await enqueue_scope_reply(
        mind.pfc, scope,
        mind.pfc.get_adapter_key(scope),
        f"后台任务{'失败' if not success else '完成'}: {description[:60]}",
        prompt,
    )
    if not mind.wake_budget.allow(scope, failed=not success):
        mind.wake_budget.note_suppressed(scope, description, failed=not success)
        return
    mind.wake_budget.consume(scope, failed=not success)
    spawn(mind.try_execute_mind(), name="mind.wake")


async def on_task_alert(
    mind: Mind, scope: str, description: str, detail: str, task_id: str,
) -> None:
    """后台任务超时提醒：超过预期时长时向 AI 报告进度事实。

    系统只报告、不下指令——超时未必是异常（大下载/大构建本来就慢），
    提醒先引导甄别"确实需要更久"还是"真的卡住"，三个去向（等/查/终止）
    平铺为可用事实，由 AI 自行判断。提醒经一次性历史固化（同完成通知通道）。
    """
    from agent.mind.tools.scheduler import enqueue_scope_reply
    prompt = (
        f"[后台任务进度报告] {description}\n"
        f"{detail}\n"
        "耗时不代表失败。"
        f"用 check_background_tasks(task_id=\"{task_id}\") 查看阶段、操作证据与通知策略；"
        f"确认无需继续时可 terminate_background_task(task_id=\"{task_id}\")。"
        "这是内部进度事实，是否跟进由你判断。"
    )
    if not scope.startswith(("user_", "group_")):
        mind.pfc.add_temporary({"role": "system", "content": prompt})
        return
    await enqueue_scope_reply(
        mind.pfc, scope,
        mind.pfc.get_adapter_key(scope),
        f"后台任务进度报告: {description[:60]}",
        prompt,
    )
    if not mind.wake_budget.allow(scope):
        mind.wake_budget.note_suppressed(scope, description)
        return
    mind.wake_budget.consume(scope)
    spawn(mind.try_execute_mind(), name="mind.wake")
