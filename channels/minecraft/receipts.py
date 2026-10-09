"""查询和停止的确定性回执；只读取工具事实，不再执行动作或调用模型。"""

import re
from collections.abc import Sequence

from agent.channel.reply_policy import ReplyToolResult
from core.tool_context import request_trace

_NON_ACTIONS = frozenset({
    "end_reply", "list_entity_methods", "query_entities", "activate_tool_group", "recall",
    "send_message", "chat", "whisper",
})
_STOPS = frozenset({"cancel_task", "stop_pathfinding", "clear_control_states", "cancel_collect"})
_BACKGROUND_TASKS = {"prepare_item": "制作", "manage_supplies": "补给", "gather_resources": "采集"}


def handed_to_game_events(results: Sequence[ReplyToolResult]) -> bool:
    """仅本请求真实受理的高层任务可交给终态事件；失败或后续动作不交接。"""
    for result in reversed(results):
        if result.name in _NON_ACTIONS:
            continue
        data = result.payload
        if result.name not in _BACKGROUND_TASKS or not isinstance(data, dict):
            return False
        origin = data.get("origin")
        request = request_trace()
        return bool(
            not data.get("error") and data.get("success") is not False and data.get("ok") is not False
            and data.get("active") is True and data.get("phase") == "running"
            and isinstance(data.get("id"), str) and data["id"]
            and isinstance(data.get("actionId"), str) and data["actionId"]
            and isinstance(origin, dict) and request.get("request_id")
            and origin.get("requestId") == request["request_id"] and origin.get("scope") == request.get("scope")
        )
    return False


def game_result_receipt(results: Sequence[ReplyToolResult]) -> str:
    """补交最后一个查询/停止结果；任何后续动作均使旧快照失效。"""
    for result in results:
        if result.name in {"chat", "whisper"}:
            data = result.payload
            if isinstance(data, dict) and data.get("ok") is True and not data.get("error"):
                return ""
    if handed_to_game_events(results):
        task = next(result for result in reversed(results) if result.name in _BACKGROUND_TASKS)
        return f"{_BACKGROUND_TASKS[task.name]}任务已接单，完成或受阻后会按实际结果告诉你。"
    for result in reversed(results):
        if result.name in _NON_ACTIONS:
            continue
        data = result.payload
        if result.name not in _STOPS and result.name != "get_inventory":
            # 制作/采集/补给/挖矿由各自终态事件播报；不重复，也不报动作前的库存。
            return ""
        if not isinstance(data, dict) or data.get("error") or data.get("success") is False or data.get("ok") is False:
            return "这次未能确认停止结果。" if result.name in _STOPS else "这次没有取得有效背包数据，数量尚未确认。"
        if result.name in _STOPS:
            return "当前动作已停止。" if data.get("stopped") is True else "停止请求已处理，动作或清理尚未确认完成。"
        return _inventory_receipt(data.get("items"), data.get("crafting"))
    return ""


def _inventory_receipt(items: object, crafting: object) -> str:
    """合并背包同名槽位；畸形或截断快照不能当作空背包。"""
    invalid = "这次背包数据不完整，数量尚未确认。"
    if not isinstance(items, list) or len(items) > 46:
        return invalid
    totals: dict[str, int] = {}
    for item in items:
        if not isinstance(item, dict):
            return invalid
        name, count = item.get("name"), item.get("count")
        if not isinstance(name, str) or not re.fullmatch(r"[a-z0-9_]{1,80}", name):
            return invalid
        if type(count) is not int or count < 0:
            return invalid
        totals[name] = totals.get(name, 0) + count
    text = "背包实际物品：" + ("、".join(f"{name}×{count}" for name, count in sorted(totals.items())) or "空")
    text += "。未列出的物品在背包中为 0；装备栏和合成栏另计。"
    if isinstance(crafting, dict):
        slots = crafting.get("slots")
        if crafting.get("cursor") or (isinstance(slots, list) and any(slots)):
            text += "合成窗口还有物品，尚未全部收回背包。"
    return text
