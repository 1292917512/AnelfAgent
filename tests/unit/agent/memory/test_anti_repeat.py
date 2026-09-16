"""防复读（anti_repeat.py）单元测试：分词 / 话题提示 / 重叠评分。"""

from __future__ import annotations

import pytest

from agent.memory.anti_repeat import (
    compute_hint_terms,
    overlap_ratio,
    repeat_score,
    tokenize,
)


class TestTokenize:
    def test_cjk_bigrams(self):
        tokens = tokenize("今晚吃什么")
        assert "今晚" in tokens and "吃什" in tokens

    def test_latin_words_lowered(self):
        tokens = tokenize("Play some Minecraft today")
        assert "minecraft" in tokens and "play" in tokens
        assert "a" not in tokens  # 单字符词不入集


class TestHintTerms:
    def test_repeated_topics_surface(self):
        texts = [
            "今天天气不错，适合出去走走",
            "天气不错的话明天去露营吧",
            "最近天气都很不错呢",
        ]
        terms = compute_hint_terms(texts, top_k=3, min_df=3)
        assert "天气" in terms or "不错" in terms

    def test_below_min_df_empty(self):
        texts = ["聊了一次的话题", "完全不同的内容", "第三段无关文本"]
        assert compute_hint_terms(texts, top_k=3, min_df=3) == []

    def test_short_history_empty(self):
        assert compute_hint_terms(["只有一条"], top_k=3, min_df=3) == []


class TestOverlap:
    def test_identical_text_full_overlap(self):
        a = "主人今天早点休息，明天还要早起开会"
        assert repeat_score(a, [a]) == pytest.approx(1.0)

    def test_disjoint_text_low_overlap(self):
        draft = "我们周末去看电影吧，最近有部新片上映"
        recent = ["今天的股市行情不太妙，注意风险"]
        assert repeat_score(draft, recent) < 0.2

    def test_gate_uses_recent_window(self):
        """闸门只与前景窗口（最近 5 条）比对：老消息重复不算复读。"""
        draft = "记得多喝水，早点休息，注意身体"
        old_duplicate = "记得多喝水，早点休息，注意身体"
        fresh = ["周末的露营计划", "新出的电影上映", "股市行情不妙", "猫咪又拆家了", "健身后的酸痛"]
        # 重复消息被 5 条新消息挤出窗口 → 低分放行
        assert repeat_score(draft, [old_duplicate] + fresh) < 0.5
        # 重复消息在窗口内 → 高分拦截
        assert repeat_score(draft, fresh[:4] + [old_duplicate]) > 0.5

    def test_empty_inputs(self):
        assert repeat_score("", ["任何"]) == 0.0
        assert repeat_score("草稿", []) == 0.0
        assert overlap_ratio(set(), {"a"}) == 0.0
