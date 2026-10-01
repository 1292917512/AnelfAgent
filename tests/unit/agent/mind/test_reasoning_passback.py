"""reasoning 字段条件回传（message_schema.preserve_reasoning_fields）单元测试。

锁定：纯文本轮丢弃 reasoning_details（DeepSeek 官方规则：普通轮服务端忽略）
但保留 thinking_blocks（Anthropic 签名块语义保守不动）；工具轮两者都保留。
双源取值：流式聚合字段优先，非流式回退 raw 响应体。
"""

from __future__ import annotations

from agent.llm.types import ChatResult
from agent.mind.message_schema import preserve_reasoning_fields


def _result(reasoning: str = "思考过程", details: bool = True, blocks: bool = True) -> ChatResult:
    message: dict = {}
    if details:
        message["reasoning_details"] = [{"type": "reasoning", "text": reasoning}]
    if blocks:
        message["thinking_blocks"] = [{"type": "thinking", "thinking": reasoning, "signature": "sig"}]
    return ChatResult(content="", raw={"choices": [{"message": message}]}, reasoning_content=reasoning)


class TestConditionalPassback:
    def test_tool_turn_keeps_both(self) -> None:
        msg: dict = {"role": "assistant", "content": "", "tool_calls": [{"id": "c1"}]}
        preserve_reasoning_fields(msg, _result(), tool_turn=True)
        assert "reasoning_details" in msg
        assert "thinking_blocks" in msg

    def test_plain_turn_drops_reasoning_details(self) -> None:
        msg: dict = {"role": "assistant", "content": "回答"}
        preserve_reasoning_fields(msg, _result(), tool_turn=False)
        assert "reasoning_details" not in msg, "纯文本轮 reasoning_details 服务端忽略，回传纯浪费"
        assert "thinking_blocks" in msg, "Anthropic 签名块保持无条件保留"

    def test_default_is_plain_turn(self) -> None:
        msg: dict = {"role": "assistant", "content": "回答"}
        preserve_reasoning_fields(msg, _result())
        assert "reasoning_details" not in msg
        assert "thinking_blocks" in msg

    def test_no_reasoning_content_noop(self) -> None:
        msg: dict = {"role": "assistant", "content": "回答"}
        preserve_reasoning_fields(
            msg, ChatResult(content="", raw={"choices": [{"message": {}}]}, reasoning_content=""))
        assert msg == {"role": "assistant", "content": "回答"}

    def test_missing_raw_noop(self) -> None:
        msg: dict = {"role": "assistant", "content": "回答"}
        preserve_reasoning_fields(msg, ChatResult(content="", raw=None, reasoning_content="x"))
        assert msg == {"role": "assistant", "content": "回答"}

    def test_malformed_raw_swallowed(self) -> None:
        msg: dict = {"role": "assistant", "content": "回答"}
        preserve_reasoning_fields(
            msg, ChatResult(content="", raw={"choices": "not-a-list"}, reasoning_content="x"))
        assert msg == {"role": "assistant", "content": "回答"}


class TestStreamingAggregatedFields:
    """流式路径：raw 为 None，推理字段来自聚合层装配（修复流式恒丢失回归）。"""

    def test_streaming_tool_turn_keeps_both(self) -> None:
        msg: dict = {"role": "assistant", "content": "", "tool_calls": [{"id": "c1"}]}
        result = ChatResult(
            content="", reasoning_content="思考过程",
            thinking_blocks=[{"type": "thinking", "thinking": "思考过程", "signature": "sig"}],
            reasoning_details=[{"type": "reasoning", "text": "思考过程"}],
        )
        preserve_reasoning_fields(msg, result, tool_turn=True)
        assert msg["reasoning_details"] == [{"type": "reasoning", "text": "思考过程"}]
        assert msg["thinking_blocks"][0]["signature"] == "sig"

    def test_streaming_plain_turn_drops_details(self) -> None:
        msg: dict = {"role": "assistant", "content": "回答"}
        result = ChatResult(
            content="", reasoning_content="思考过程",
            thinking_blocks=[{"type": "thinking", "thinking": "思考过程", "signature": "sig"}],
            reasoning_details=[{"type": "reasoning", "text": "思考过程"}],
        )
        preserve_reasoning_fields(msg, result, tool_turn=False)
        assert "reasoning_details" not in msg
        assert "thinking_blocks" in msg
