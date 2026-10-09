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

from typing import TYPE_CHECKING, Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict

if TYPE_CHECKING:
    from agent.llm.types import ChatResult


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


def ensure_tool_result_pairing(messages: List[Dict]) -> List[Dict]:
    """tool_use/tool_result 配对铁律。

    发送前最后一道防线：
    - assistant 的 tool_calls 缺少对应 role=tool 结果 → 合成错误结果
      （执行被中断/取消），避免提供商 400
    - 无对应 tool_calls 的孤儿 role=tool 消息 → 剔除
    - tool_call_id 为空字符串的条目 → 合成临时 id 并补齐配对
      （端点对缺 id 的 tool_call 会重建 <name>:<index> 内部 ID，
      与本地 tool_call_id 不匹配导致 400）
    """
    # 收集全部 tool_call id 与已有结果 id；空 id 的 tool_call 替换为合成 id
    call_ids: List[str] = []
    result_ids: set = set()
    synth_seq = 0
    normalized: List[Dict] = []
    for msg in messages:
        if msg.get("role") == "assistant" and msg.get("tool_calls"):
            fixed_tcs: List[Dict] = []
            for tc in msg["tool_calls"]:
                if not isinstance(tc, dict):
                    fixed_tcs.append(tc)
                    continue
                tc_id = tc.get("id")
                if not tc_id:
                    synth_seq += 1
                    tc_id = f"tc_synth_{synth_seq}"
                    tc = {**tc, "id": tc_id}
                fixed_tcs.append(tc)
                call_ids.append(tc_id)
            normalized.append({**msg, "tool_calls": fixed_tcs})
            continue
        if msg.get("role") == "tool":
            tc_id = msg.get("tool_call_id")
            if tc_id:
                result_ids.add(tc_id)
        normalized.append(msg)

    call_id_set = set(call_ids)
    # 1. 剔除孤儿 tool 结果（含 tool_call_id 为空/缺失的——它们无法配对任何
    # tool_call，发送给端点会被 litellm 转换层静默丢弃（call_id 空 → skip），
    # 导致端点看到 function_call 缺响应而 400）
    cleaned = [
        msg for msg in normalized
        if not (
            msg.get("role") == "tool"
            and (not msg.get("tool_call_id") or msg["tool_call_id"] not in call_id_set)
        )
    ]

    # 2. 为缺失结果的 tool_calls 合成错误结果（紧跟对应 assistant 消息之后）
    missing = [cid for cid in call_ids if cid not in result_ids]
    if not missing:
        return cleaned
    missing_set = set(missing)
    repaired: List[Dict] = []
    for msg in cleaned:
        repaired.append(msg)
        if msg.get("role") == "assistant" and msg.get("tool_calls"):
            for tc in msg["tool_calls"]:
                tc_id = tc.get("id") if isinstance(tc, dict) else None
                if tc_id and tc_id in missing_set:
                    repaired.append({
                        "role": "tool",
                        "tool_call_id": tc_id,
                        "content": '{"error": "工具执行被中断或取消，未产生结果"}',
                    })
    return repaired


# 发送前剥离的内部元数据键（LLM 不可见）：
# - _layer：上下文分层标签（快照分类 / 缓存断点锚点用）
# - _source：系统注入消息的结构化来源标记（归因 / 审计用）
_STRIP_KEYS = ("_layer", "_source")


def normalize_for_send(messages: List[Dict]) -> List[Dict]:
    """发送边界统一规整：配对修复 + 角色归一 + 尾部 prefill 修复 + 空 content 修复。

    _invoke_llm_unified 的唯一入口；新增发送边界规则只加在这里，
    不再散落到上下文组装各阶段。
    """
    # 剥离内部分类标签（上下文快照用，LLM 不可见）。
    # 注意必须非破坏式：llm_messages 与 ctx.base_messages 共享 dict 对象，
    # 原地 pop 会导致首轮发送后 base_messages 永久丢失分层标签（快照分类失真）
    stripped = [
        {k: v for k, v in m.items() if k not in _STRIP_KEYS}
        if any(k in m for k in _STRIP_KEYS) else m
        for m in messages
    ]
    return fix_empty_tool_call_content(
        fix_trailing_assistant(normalize_roles(ensure_tool_result_pairing(stripped)))
    )


# ------------------------------------------------------------------
# 消息内容判定与推理字段回传（纯函数叶子区）
# ------------------------------------------------------------------

# 真用户原话判定：经渠道到达的消息 content 以前缀元数据标签开头
# （[time:…][uid:…][name:…] 等，见 messages.everything.get_tag_list）。
# 机器生成的 user 角色消息（proactive 主动联系指令、自主操作提示、
# prefill 修复后被改写为 user 的 assistant 独白等）没有到达标签——
# 它们属于执行块而非用户原话。
_USER_ARRIVAL_TAGS = (
    "time", "uid", "name", "channel", "group_id", "session_id", "message_id",
)

# 平台事件/推送的 kind 标签（渲染于元数据标签之后）：此类消息虽经渠道到达，
# 但不是用户原话或请求，不应进入未回复补回等"欠回复"语义
_NON_CHAT_KIND_MARKERS = ("[kind:event]", "[kind:notification]", "[kind:system]")


def is_genuine_user_message(msg: Dict) -> bool:
    """判定 role=user 消息是否为真用户原话（渠道到达、带元数据标签）。"""
    if msg.get("role") != "user":
        return False
    content = msg.get("content")
    if not isinstance(content, str) or not content:
        return False
    head = content.lstrip()[:200]
    if not (head.startswith("[") and any(f"[{tag}:" in head for tag in _USER_ARRIVAL_TAGS)):
        return False
    # kind 标签渲染在元数据标签串之后，窗口放宽覆盖其位置
    return not any(marker in content[:400] for marker in _NON_CHAT_KIND_MARKERS)


def preserve_reasoning_fields(msg: Dict[str, Any], result: "ChatResult",
                              tool_turn: bool = False) -> None:
    """从 ChatResult 提取推理字段到 assistant 消息，维持多轮思维链。

    litellm 统一返回 OpenAI 格式，按协议覆盖两种载体：
    - reasoning_details：OpenRouter 风格，litellm 请求侧原样回传。
      **仅 tool_turn=True（工具调用轮）挂载**——DeepSeek 官方 thinking 规则
      要求工具轮回传、纯文本轮服务端直接忽略，普通轮回传纯属 token 浪费
      （REFLECT 连续文本轮场景收益最大）
    - thinking_blocks：Anthropic 协议 thinking 块（含 signature/redacted），
      litellm 请求侧据此重构 thinking 块（交错思考 + tool_use 场景必需）。
      签名块语义微妙，保持无条件保留（不随本参数收紧，单独评估）
    双源取值：流式聚合字段（thinking_blocks/reasoning_details）优先，
    非流式回退 raw 响应体——两载体行为一致。均以响应实际存在为条件，
    不返回推理字段的模型行为不变。
    """
    if not result.reasoning_content:
        return
    rd = result.reasoning_details
    tb = result.thinking_blocks
    if (rd is None or tb is None) and result.raw:
        try:
            choices = result.raw.get("choices")
            if choices and isinstance(choices, list):
                message = choices[0].get("message", {})
                if isinstance(message, dict):
                    if rd is None:
                        rd = message.get("reasoning_details")
                    if tb is None:
                        tb = message.get("thinking_blocks")
        except (IndexError, AttributeError, TypeError):
            pass
    if tool_turn and rd:
        msg["reasoning_details"] = rd
    if tb:
        msg["thinking_blocks"] = tb
