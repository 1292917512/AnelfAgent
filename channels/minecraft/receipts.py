"""游戏后台任务的交接判定，只采信本次请求的真实受理回执。"""

from collections.abc import Sequence

from agent.channel.reply_policy import ReplyToolResult
from core.tool_context import request_trace

_NON_ACTIONS = frozenset({
    "end_reply", "list_entity_methods", "query_entities", "activate_tool_group", "recall",
    "send_message", "chat", "whisper",
})
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
