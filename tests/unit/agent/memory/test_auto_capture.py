"""自动捕获与写入去重的纯函数单元测试（无 LLM 依赖）。"""

from __future__ import annotations

from agent.memory.auto_capture import parse_extraction, should_extract
from agent.memory.dedup import parse_judgement


class TestShouldExtract:
    def test_substantive_batch_passes(self) -> None:
        msgs = [
            {"role": "user", "content": "我上周五去看了演唱会，特别开心"},
            {"role": "assistant", "content": "听起来很棒，是谁的演唱会呀"},
        ]
        assert should_extract(msgs)

    def test_chitchat_batch_filtered(self) -> None:
        msgs = [
            {"role": "user", "content": "哈哈"},
            {"role": "assistant", "content": "嗯嗯"},
            {"role": "user", "content": "好"},
        ]
        assert not should_extract(msgs)


class TestParseExtraction:
    def test_valid_array(self) -> None:
        raw = '[{"content": "主人不吃辣", "type": "fact", "topic": "饮食", "importance": 0.8, "sensitivity": "normal"}]'
        items = parse_extraction(raw, max_items=6)
        assert len(items) == 1
        assert items[0]["type"] == "fact"
        assert items[0]["importance"] == 0.8

    def test_empty_and_noise(self) -> None:
        assert parse_extraction("[]", max_items=6) == []
        assert parse_extraction("没有可提取内容", max_items=6) == []

    def test_field_sanitization(self) -> None:
        raw = '''[
          {"content": "短", "type": "fact"},
          {"content": "正常的记忆内容", "type": "weird", "importance": 99, "sensitivity": "Private"},
          {"content": null}
        ]'''
        items = parse_extraction(raw, max_items=6)
        assert len(items) == 1
        assert items[0]["type"] == "fact"  # 非法类型归一
        assert items[0]["importance"] == 1.0  # 超界截断
        assert items[0]["sensitivity"] == "private"  # 大小写归一

    def test_max_items_cap(self) -> None:
        raw = "[" + ",".join(
            f'{{"content": "记忆内容{i}号"}}' for i in range(10)
        ) + "]"
        assert len(parse_extraction(raw, max_items=3)) == 3

    def test_trailing_comma_repair(self) -> None:
        raw = '[{"content": "主人喜欢猫",}]'
        items = parse_extraction(raw, max_items=6)
        assert len(items) == 1


class TestParseJudgement:
    def test_store(self) -> None:
        d = parse_judgement('{"action": "store", "reason": "无重复"}', {1, 2})
        assert d and d["action"] == "store"

    def test_skip(self) -> None:
        d = parse_judgement('前缀文字 {"action": "skip"} 后缀', {1})
        assert d and d["action"] == "skip"

    def test_update_valid(self) -> None:
        d = parse_judgement(
            '{"action": "update", "target_id": 2, "content": "合并后的内容"}', {1, 2},
        )
        assert d and d["action"] == "update" and d["target_id"] == 2

    def test_update_invalid_target_falls_back_to_store(self) -> None:
        d = parse_judgement(
            '{"action": "update", "target_id": 99, "content": "内容"}', {1, 2},
        )
        assert d and d["action"] == "store"

    def test_update_empty_content_falls_back_to_store(self) -> None:
        d = parse_judgement('{"action": "update", "target_id": 1, "content": ""}', {1})
        assert d and d["action"] == "store"

    def test_garbage_returns_none(self) -> None:
        assert parse_judgement("不是JSON", {1}) is None
        assert parse_judgement('{"action": "explode"}', {1}) is None
