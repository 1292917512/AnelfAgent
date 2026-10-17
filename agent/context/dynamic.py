"""动态执行提示的文本呈现；不读取或修改思维状态。

模型可见：本轮状态、发送事实与输出纪律，文案及顺序不变。
Token：不增加提示或模型调用。
缓存：只补充每轮动态尾部，不修改稳定前缀。
"""


from __future__ import annotations

from agent.channel.delivery import DeliveryStatus, delivery_tags
from core.config import get_config_bool

_PROMPT_REPLY_GUIDE = (
    "[输出契约]\n"
    "1. 先做事再说话：需要查资料或执行操作时，立刻通过 function calling 调用工具；"
    "可并行的独立工具同一轮一并发起。\n"
    "2. 给用户的答复使用 send_message 显式投递；跨会话和媒体用对应发送工具。"
    "发送工具不经过正文自动审核，也不结束执行；工具结果与未投递正文仅内部可见。"
    "核对目标与真实回执，不要以普通正文代替发送。\n"
    "3. 已回复、无需回复或内部处理完成时直接调用 end_reply；结束备注和同批正文不投递。"
    "不要重复已送达的答复，内部核验、自我指令与推理不作为对用户的正文。"
)


_PROMPT_REFLECT_OUTPUT_DISCIPLINE = (
    "[输出纪律] 你必须严格遵守：\n"
    "1. 需要执行动作（检索/查询/分析）时立即调用对应工具，禁止只用文字描述动作\n"
    "2. 没有默认投递会话，普通正文只留在内部执行上下文，不会自动发送或记为用户已收到\n"
    "3. 完成分析后调用 end_reply 结束本轮反思"
)


def format_delivery_status(
    scope: str, delivery_status: DeliveryStatus, *, output_sent: bool,
    auto_reply_blocked: bool, auto_reply_eligible: bool,
) -> str:
    """当前会话的发送事实与尚未投递的正文状态。"""
    tags = delivery_tags("channel", delivery_status, scope)
    status = "当前会话已有成功发送回执。" if output_sent else "当前会话尚无成功发送回执。"
    if auto_reply_blocked:
        status += "没有新的工作或用户消息时不重复发送收尾正文；已完成请直接 end_reply。"
    elif auto_reply_eligible:
        interim = "开启" if get_config_bool("send_interim_text", False) else "关闭"
        final = "开启" if get_config_bool("send_final_text", True) else "关闭"
        status += (f"系统正文兜底：中间{interim}、最终{final}；开启不等于送达。"
                   "需要回复时用 send_message，不能依赖正文兜底。")
    else:
        status += "本轮由内部事件触发，不自动投递；需要通知用户时显式使用 send_message。"
    return "[投递状态] " + tags + status


def append_execution_status(
    message: dict, *, background: str, reply: bool,
    delivery: str = "", output_blocked: bool = False,
) -> None:
    """按后台状态、发送事实、输出纪律的顺序补充动态执行块。"""
    if background:
        message["content"] += "\n" + background
    if reply:
        message["content"] += "\n" + delivery
    message["content"] += "\n" + (_PROMPT_REPLY_GUIDE if reply else _PROMPT_REFLECT_OUTPUT_DISCIPLINE)
    if not reply:
        message["content"] += "\n" + delivery_tags("internal", "pending") + (
            "本次任务明确禁用对外发送。" if output_blocked else
            "需要对外通知时可以显式调用 send_message，必须指定已核实的频道与会话并核对回执；"
            "跨频道消息只是感知，不会改变本轮的投递目标。"
        )
