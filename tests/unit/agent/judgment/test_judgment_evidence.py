"""自动判断证据的预算、归属、去重与多模态边界。"""

from copy import deepcopy

import pytest

from agent.judgment.evidence import conversation_evidence, evidence_json
from core.config import ConfigManager


@pytest.mark.parametrize("budget", [600, 1500, 3000])
def test_shared_budget_preserves_latest_request_and_does_not_mutate_messages(budget: int) -> None:
    ConfigManager.set("judgment_evidence_max_chars", budget)
    messages = [
        {"role": "system", "content": "stable-secret"},
        {"role": "user", "content": "[channel:qq][uid:42] 请取消任务。" + "细节" * 3000},
        *({"role": "tool", "content": f"工具 {n}: " + '日志\\\n"' * 3000} for n in range(20)),
        {"role": "user", "content": "provider-private-state", "_source": {"origin": "context_provider"}},
    ]
    original = deepcopy(messages)
    evidence = conversation_evidence(messages)
    encoded = evidence_json(evidence)
    assert len(encoded) <= budget
    assert "[channel:qq][uid:42] 请取消任务。" in encoded
    assert "stable-secret" not in encoded and "provider-private-state" not in encoded
    assert "truncated" in encoded
    assert messages == original


def test_delivery_fact_is_not_duplicated_and_candidate_is_not_reinjected() -> None:
    messages = [
        {"role": "assistant", "content": "已完成旧任务", "_source": {"origin": "conversation", "id": 1}},
        {"role": "user", "content": "[uid:42] 开始新任务"},
        *({"role": "tool", "content": f"result {n}"} for n in range(8)),
        {"role": "assistant", "content": "正在执行新任务"},
    ]
    evidence = conversation_evidence(messages, include_delivered=True, exclude_content="正在执行新任务")
    encoded = evidence_json(evidence)
    assert encoded.count("已完成旧任务") == 1 and "正在执行新任务" not in encoded
    assert evidence[0] == {"role": "assistant", "content": "已完成旧任务", "delivered": True}
    assert "开始新任务" in encoded and "result 7" in encoded


def test_text_blocks_and_tool_names_survive_without_serializing_media_payloads() -> None:
    messages = [
        {"role": "user", "content": [{"type": "text", "text": "[channel:webui][uid:web_user] 看这张图片"},
                                      {"type": "image_url", "image_url": {"url": "data:image/png;base64," + "A" * 100000}}]},
        {"role": "assistant", "tool_calls": [{"id": "irrelevant-call-id", "function": {
            "name": "inspect_image", "arguments": '{"path":"photo.png","payload":"' + "B" * 100000 + '"}',
        }}]},
        {"role": "tool", "content": "识别失败，请检查路径"},
    ]
    encoded = evidence_json(conversation_evidence(messages))
    assert "看这张图片" in encoded and "inspect_image" in encoded and "识别失败" in encoded
    assert "data:image/png" not in encoded and "irrelevant-call-id" not in encoded
    assert len(encoded) < 1000


def test_config_changes_apply_without_retaining_another_conversation() -> None:
    source = [{"role": "user", "content": "[uid:42] " + "x" * 800},
              {"role": "tool", "content": "result" * 500}]
    before = conversation_evidence(source)
    ConfigManager.set("judgment_evidence_max_chars", 600)
    after = conversation_evidence(source)
    assert len(evidence_json(after)) < len(evidence_json(before))
    assert conversation_evidence([]) == []
