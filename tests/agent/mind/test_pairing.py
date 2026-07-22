"""tool_use/tool_result 配对铁律测试（发送边界最后一道防线）。"""

from __future__ import annotations

from agent.mind.message_schema import ensure_tool_result_pairing, normalize_for_send


def _assistant_with_calls(*ids: str) -> dict:
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {"id": i, "type": "function", "function": {"name": "t", "arguments": "{}"}}
            for i in ids
        ],
    }


class TestPairingRepair:
    def test_missing_result_synthesized(self):
        messages = [
            {"role": "user", "content": "hi"},
            _assistant_with_calls("a", "b"),
            {"role": "tool", "tool_call_id": "a", "content": "ok"},
        ]
        out = ensure_tool_result_pairing(messages)
        tool_msgs = [m for m in out if m["role"] == "tool"]
        assert len(tool_msgs) == 2
        synth = [m for m in tool_msgs if m["tool_call_id"] == "b"][0]
        assert "中断" in synth["content"]
        # 合成结果紧跟 assistant 消息
        idx_assistant = next(i for i, m in enumerate(out) if m.get("tool_calls"))
        assert out[idx_assistant + 1]["tool_call_id"] in ("a", "b")

    def test_orphan_result_removed(self):
        messages = [
            _assistant_with_calls("a"),
            {"role": "tool", "tool_call_id": "a", "content": "ok"},
            {"role": "tool", "tool_call_id": "ghost", "content": "???"},
        ]
        out = ensure_tool_result_pairing(messages)
        assert all(m.get("tool_call_id") != "ghost" for m in out)

    def test_complete_pairing_untouched(self):
        messages = [
            _assistant_with_calls("a"),
            {"role": "tool", "tool_call_id": "a", "content": "ok"},
            {"role": "user", "content": "next"},
        ]
        out = ensure_tool_result_pairing(messages)
        assert out == messages

    def test_multiple_assistant_rounds(self):
        messages = [
            _assistant_with_calls("a"),
            {"role": "tool", "tool_call_id": "a", "content": "ok"},
            _assistant_with_calls("b", "c"),
            {"role": "tool", "tool_call_id": "c", "content": "ok"},
        ]
        out = ensure_tool_result_pairing(messages)
        ids = [m["tool_call_id"] for m in out if m["role"] == "tool"]
        assert sorted(ids) == ["a", "b", "c"]


class TestNormalizeForSendIntegration:
    def test_pairing_repaired_before_send(self):
        messages = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "hi"},
            _assistant_with_calls("a"),
        ]
        out = normalize_for_send(messages)
        assert any(m["role"] == "tool" and m["tool_call_id"] == "a" for m in out)
