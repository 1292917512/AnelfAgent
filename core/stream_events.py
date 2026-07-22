"""流式与对话窗口事件契约 — 内核流式事件 + webui SSE 事件的统一定义。

设计（P5-C3）：
- 内核事件（event_bus）：think_loop 流式产生的过程事件，通道按能力订阅
- SSE 事件（webui 通道 → 前端）：webui 把内核事件翻译为 SSE 帧
- 两侧 payload schema 在此集中定义（TypedDict），前后端共享契约

多频道语义约束：这些事件全部是**过程性**的（不落持久对话历史），
回复出口仍是 send_message/end_reply 工具（多频道语义不变）。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, TypedDict

# ------------------------------------------------------------------
# 内核事件名（event_bus）
# ------------------------------------------------------------------

#: assistant 文本增量（流式 LLM 输出）
EVENT_ASSISTANT_DELTA = "assistant_delta"
#: 工具调用流式生命周期（参数增量聚合 → 完整调用就绪）
EVENT_TOOL_CALL_DELTA = "tool_call_delta"
EVENT_TOOL_CALL_READY = "tool_call_ready"
#: 上下文用量快照（token 数 / 阈值 / 百分比）
EVENT_CONTEXT_USAGE = "context_usage"
#: 文件编辑 diff（edit_file 成功后发出，供 webui 展示；不进模型上下文）
EVENT_FILE_DIFF = "file_diff"


# ------------------------------------------------------------------
# 内核事件 payload
# ------------------------------------------------------------------

class AssistantDeltaPayload(TypedDict, total=False):
    """assistant 文本增量事件。"""

    scope: str            # 对话 scope（通道据此路由）
    turn_id: str          # 本轮思维会话标识（前端聚合 key）
    delta: str            # 文本增量
    accumulated: str      # 截至目前累计文本（供迟到订阅者对齐）
    reasoning: bool       # 是否为推理（thinking）增量


class ToolCallDeltaPayload(TypedDict, total=False):
    """工具调用参数增量事件（流式聚合过程）。"""

    scope: str
    turn_id: str
    call_id: str          # 工具调用 id（provider 分配或流式序号）
    name: str             # 工具名（首个 delta 时确定）
    arguments_delta: str  # 参数 JSON 增量


class ToolCallReadyPayload(TypedDict, total=False):
    """完整工具调用就绪事件（即将进入执行管线）。"""

    scope: str
    turn_id: str
    call_id: str
    name: str
    arguments: str        # 完整参数 JSON


class FileDiffPayload(TypedDict, total=False):
    """文件编辑 diff 事件（过程性展示，不落历史）。"""

    scope: str
    path: str
    diff: str            # unified diff（已截断）
    additions: int
    removals: int


class ContextUsagePayload(TypedDict, total=False):
    """上下文用量快照事件。"""

    scope: str
    tokens: int           # 当前估算 token 数
    threshold: int        # 压缩触发阈值
    window: int           # 模型上下文窗口
    percent: float        # tokens / threshold × 100（0-100+）


# ------------------------------------------------------------------
# webui SSE 事件名（/chat/stream 帧类型）
# ------------------------------------------------------------------

SSE_DELTA = "delta"                    # AssistantDeltaPayload 子集 {turn_id, delta, reasoning}
SSE_TOOL_CALL = "tool_call"            # {turn_id, call_id, name, status, arguments?}
SSE_CONTEXT_USAGE = "context_usage"    # ContextUsagePayload
SSE_APPROVAL_REQUEST = "approval_request"  # 审批弹窗（P1.5 已实现）
SSE_FILE_DIFF = "file_diff"                # FileDiffPayload


class SseToolCallFrame(TypedDict, total=False):
    """SSE 工具调用帧（前端内联工具块，status: running/done/error）。"""

    turn_id: str
    call_id: str
    name: str
    status: str
    arguments: str
    result_preview: str
    diff: str             # edit_file 等工具的 unified diff（可选）
