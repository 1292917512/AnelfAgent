"""内部消息契约与发送边界规整层（参考 Mini-Agent schema + 提供商转换层思路）。

背景：发送边界的修补逻辑历史上散落多处——
- mind._normalize_message_roles：中途 system 注入转 user
  （anthropic 协议会把任意位置的 system 消息抽离到 system 参数顶部，
  不转换则中途纠正/反馈全部脱离上下文位置）
- prefrontal_cortex.build_llm_context 末尾：尾部 assistant 转 user（prefill 400）

本模块将两类修补收拢为单一入口 normalize_for_send()，任何新的发送边界
规则只加在这里；并用 pydantic 模型定义内部消息契约（文档化 + 可校验）。

上下文组装全程保持 dict（litellm 线格式即 dict，零转换成本，提供商差异
由 litellm 在 API 边界吸收）；ChatMessage 等模型用于构造、校验与测试。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict


class FunctionCall(BaseModel):
    """工具调用的函数部分。"""

    model_config = ConfigDict(extra="allow")

    name: str = ""
    arguments: Any = ""


class ToolCall(BaseModel):
    """一次工具调用（OpenAI function-calling 线格式）。"""

    model_config = ConfigDict(extra="allow")

    id: str = ""
    type: str = "function"
    function: FunctionCall = FunctionCall()


class ChatMessage(BaseModel):
    """内部消息契约：上下文组装各阶段传递的消息结构。

    content 为 str 或 block 列表（视觉图片等）；reasoning_details /
    thinking_blocks / cache_control 等提供商扩展字段经 extra="allow" 透传。
    """

    model_config = ConfigDict(extra="allow")

    role: str
    content: Any = ""
    tool_calls: Optional[List[Dict[str, Any]]] = None
    tool_call_id: Optional[str] = None
    name: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """导出为线格式 dict（None 字段省略，扩展字段保留）。"""
        return self.model_dump(exclude_none=True)

    # ---- 构造辅助 ----

    @classmethod
    def system(cls, content: str, **extra: Any) -> "ChatMessage":
        return cls(role="system", content=content, **extra)

    @classmethod
    def user(cls, content: Any, **extra: Any) -> "ChatMessage":
        return cls(role="user", content=content, **extra)

    @classmethod
    def assistant(cls, content: Any = "", **extra: Any) -> "ChatMessage":
        return cls(role="assistant", content=content, **extra)

    @classmethod
    def tool_result(cls, tool_call_id: str, content: str, **extra: Any) -> "ChatMessage":
        return cls(role="tool", content=content, tool_call_id=tool_call_id, **extra)


def validate_messages(messages: List[Dict]) -> List[Dict]:
    """按契约校验消息列表（结构非法时抛 ValidationError），返回规范化 dict。

    发送路径默认不逐条校验（大上下文性能考虑），供测试与调试使用。
    """
    return [ChatMessage.model_validate(m).to_dict() for m in messages]


def normalize_roles(messages: List[Dict]) -> List[Dict]:
    """角色归一：头部连续 system 块之后的 system 消息统一转为 user。

    头部 system 块（stable/context 提示词分层）保持不变，供 Anthropic 前缀缓存复用；
    中途的 system 注入（纠正提示/执行反馈/执行上下文/历史元消息）转为 user 角色——
    anthropic 协议端点会把任意位置的 system 消息抽离到 system 参数顶部，
    不转换则中途反馈全部脱离上下文位置；OpenAI 兼容端点对 user 角色注入同样兼容。
    内容与顺序不变，不产生消息丢失。
    """
    normalized: List[Dict] = []
    head_system = True
    for msg in messages:
        if msg.get("role") != "system":
            head_system = False
            normalized.append(msg)
        elif head_system:
            normalized.append(msg)
        else:
            normalized.append({**msg, "role": "user"})
    return normalized


def fix_trailing_assistant(messages: List[Dict]) -> List[Dict]:
    """尾部 prefill 修复：最后一条非 system 消息若是 assistant，转为 user。

    Anthropic 端点将末尾 assistant 视为 prefill（要求模型接着写），
    与工具调用/正常生成流程冲突时报 400。就地修复并返回同一列表。
    """
    for i in range(len(messages) - 1, -1, -1):
        msg = messages[i]
        if msg.get("role") == "system":
            continue
        if msg.get("role") == "assistant":
            messages[i] = {**msg, "role": "user"}
        break
    return messages


def fix_empty_tool_call_content(messages: List[Dict]) -> List[Dict]:
    """空 content 修复：带 tool_calls 的 assistant 消息空 content 置 None。

    模型只调用工具、无文本输出时 content 为 ""；anthropic 协议端点拒绝空
    文本块，litellm 会注入占位文本 "[System: Empty message content
    sanitised...]"——污染上下文且会被模型复述为用户可见的垃圾输出。
    content=None 时转换层只输出 tool_use 块，协议合法且零污染。
    """
    for i, msg in enumerate(messages):
        if (
            msg.get("role") == "assistant"
            and msg.get("tool_calls")
            and isinstance(msg.get("content"), str)
            and not msg["content"].strip()
        ):
            messages[i] = {**msg, "content": None}
    return messages


def normalize_for_send(messages: List[Dict]) -> List[Dict]:
    """发送边界统一规整：角色归一 + 尾部 prefill 修复 + 空 content 修复。

    _invoke_llm_unified 的唯一入口；新增发送边界规则只加在这里，
    不再散落到上下文组装各阶段。
    """
    return fix_empty_tool_call_content(fix_trailing_assistant(normalize_roles(messages)))
