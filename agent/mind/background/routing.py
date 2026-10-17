"""后台任务完成结果与超时提醒的会话路由。

模型看到：完成结果与进度事实经所属会话历史送达，后续行动由主模型决定。
Token：取消次数抑制后可能增加主模型处理轮次；不增加独立判断调用。
缓存：通知追加到历史尾部，写入失败回退短期记忆；不改写稳定前缀。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from agent.messages import is_conversation_scope
from core.async_helper import spawn

if TYPE_CHECKING:
    from agent.mind.mind import Mind


async def on_task_unclaimed(
    mind: Mind, scope: str, description: str, summary: str, success: bool = True,
) -> None:
    """后台任务轮外完成回调：完成事实写对话历史并排入回复队列触发新 REPLY。

    通知经 PrefrontalCortex.enqueue_reply 落目标会话的对话历史（一次性事实固化，
    不驻留短期记忆——驻留会每轮催促已处理完的事项且反复打断缓存前缀）。
    非 conversation scope（心跳任务等无会话上下文发起的任务）无处写历史，
    退化为全局短期记忆桶（对齐 PushHub 全局兜底），任何下一周期可见。

    会话通知不依赖心跳或新的用户输入，完成事实入队后尝试唤醒主模型，
    由主模型结合目标与结果决定继续、重试、回复或结束。
    """
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

    if not is_conversation_scope(scope):
        mind.pfc.add_temporary({"role": "system", "content": prompt})
        return
    await mind.pfc.enqueue_reply(
        scope,
        mind.pfc.get_adapter_key(scope),
        f"后台任务{'失败' if not success else '完成'}: {description[:60]}",
        prompt,
    )
    spawn(mind.try_execute_mind(), name="mind.wake")


async def on_task_alert(
    mind: Mind, scope: str, description: str, detail: str, task_id: str,
) -> None:
    """后台任务超时提醒：超过预期时长时向 AI 报告进度事实。

    系统只报告、不下指令——超时未必是异常（大下载/大构建本来就慢），
    提醒先引导甄别"确实需要更久"还是"真的卡住"，三个去向（等/查/终止）
    平铺为可用事实，由 AI 自行判断。提醒经一次性历史固化（同完成通知通道）。
    """
    prompt = (
        f"[后台任务进度报告] {description}\n"
        f"{detail}\n"
        "耗时不代表失败。"
        f"用 check_background_tasks(task_id=\"{task_id}\") 查看阶段、操作证据与通知策略；"
        f"确认无需继续时可 terminate_background_task(task_id=\"{task_id}\")。"
        "这是内部进度事实，是否跟进由你判断。"
    )
    if not is_conversation_scope(scope):
        mind.pfc.add_temporary({"role": "system", "content": prompt})
        return
    await mind.pfc.enqueue_reply(
        scope,
        mind.pfc.get_adapter_key(scope),
        f"后台任务进度报告: {description[:60]}",
        prompt,
    )
    spawn(mind.try_execute_mind(), name="mind.wake")
