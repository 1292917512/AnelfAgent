"""话题新鲜度按独立回复计数，保留短回复并识别重复开场。"""

from __future__ import annotations

import pytest

from agent.memory.anti_repeat import build_repeat_hint, compute_hint_terms


class TestHintTerms:
    def test_repeated_topics_surface(self):
        texts = [
            "今天天气不错，适合出去走走",
            "天气不错的话明天去露营吧",
            "最近天气都很不错呢",
        ]
        terms = compute_hint_terms(texts, top_k=3, min_df=3)
        assert "天气" in terms or "不错" in terms

    @pytest.mark.parametrize("name", ["馆长", "主人", "Captain"])
    def test_topic_depends_on_use_in_the_reply(self, name):
        replies = [f"{name}——，我试了一次缓存。", f"{name}，我看了一次缓存。", f"{name}，我查了一次缓存。"]
        assert compute_hint_terms(replies, top_k=8, min_df=3) == ["缓存"]
        replies = [f"故事里的{name}找到了钥匙。", f"他们正在寻找{name}。", f"请讨论{name}的动机。"]
        assert compute_hint_terms(replies, top_k=8, min_df=3) == [name.casefold()]

    def test_below_min_df_empty(self):
        texts = ["聊了一次的话题", "完全不同的内容", "第三段无关文本"]
        assert compute_hint_terms(texts, top_k=3, min_df=3) == []

    def test_short_history_empty(self):
        assert compute_hint_terms(["只有一条"], top_k=3, min_df=3) == []

    def test_standalone_terms_in_answers_and_headings_are_topics(self) -> None:
        replies = ["缓存。", "缓存：容量已调整。", "已查明原因。缓存！"]
        assert "缓存" in compute_hint_terms(replies, top_k=5, min_df=3)

    def test_many_occurrences_in_one_reply_are_one_vote(self) -> None:
        replies = ["数据库 " * 100, "明天去野餐", "园艺工具已收好"]
        assert compute_hint_terms(replies, top_k=2, min_df=2) == []

    def test_repeated_opening_sentence_keeps_its_topics(self) -> None:
        replies = [f"缓存容量有变化，{body}。" for body in ("继续检查", "已经记录", "明天复核")]
        assert "缓存" in compute_hint_terms(replies, top_k=5, min_df=3)


async def test_hint_counts_latest_assistant_rows_in_the_requested_scope(sqlite) -> None:
    from core.config import ConfigManager

    ConfigManager.set("memory_repeat_window", 3)
    ConfigManager.set("memory_repeat_hint_min_df", 3)
    for _ in range(3):
        await sqlite.append_conversation(scope_type="user", scope_id="123", role="assistant", content="缓存。")
        await sqlite.append_conversation(scope_type="group", scope_id="123", role="assistant", content="园艺。")
    for _ in range(10):
        await sqlite.append_conversation(scope_type="user", scope_id="123", role="user", content="无关背景")
    hint = await build_repeat_hint(sqlite, scope_type="user", scope_id="123")
    assert "缓存" in hint and "园艺" not in hint and "背景" not in hint
    ConfigManager.set("memory_repeat_hint_min_df", 4)
    assert not await build_repeat_hint(sqlite, scope_type="user", scope_id="123")
