"""检索窗口保留证据、原文位置与字符预算。"""

import pytest

from core.text_passage import passage_around, select_passage


@pytest.mark.parametrize("term", ["目标证据", "needle", "İstanbul"])
def test_tail_evidence_and_source_offsets(term: str) -> None:
    text = "无关内容\n" * 180 + f"结论：{term}，需要保留。\n" + "附录\n" * 60
    passage = select_passage(text, [term], max_chars=90)
    assert term in passage.text
    assert len(passage.text) <= 90
    assert text[passage.start:passage.end] in passage.text
    assert passage.start > 0


def test_distinct_terms_beat_repeated_noise() -> None:
    text = "alpha " * 150 + "\n" + "padding " * 100 + "\nalpha beta gamma\n"
    passage = select_passage(text, ["alpha", "beta", "gamma"], max_chars=70)
    assert "alpha beta gamma" in passage.text


def test_window_never_discards_match_to_align_lines() -> None:
    text = "header\n" + "x" * 800 + "needle\n" + "tail\n" * 50
    start = text.index("needle")
    passage = passage_around(text, start, start + 6, max_chars=40)
    assert "needle" in passage.text
    assert passage.text.startswith("…")
    assert len(passage.text) <= 40


def test_short_text_and_no_literal_match() -> None:
    assert select_passage("完整原文", ["原文"]).text == "完整原文"
    text = "原文" * 500
    passage = select_passage(text, ["无字面匹配"], max_chars=50)
    assert passage.start == 0
    assert passage.text.endswith("…")
    assert len(passage.text) <= 50


def test_oversized_match_stays_bounded() -> None:
    text = "x" * 1000
    passage = passage_around(text, 0, 900, max_chars=30)
    assert passage.start == 0
    assert len(passage.text) <= 30


def test_passage_keeps_adjacent_qualification_and_original_text() -> None:
    evidence = "约束：不要对所有站点启用代理。\nssh_exec 默认直连，仅在调用参数明确要求时使用代理。"
    text = "背景信息\n" * 100 + evidence + "\n其他内容\n" * 100
    passage = select_passage(text, ["ssh_exec"], max_chars=250)
    assert evidence in passage.text
    assert passage.text.startswith("…") and passage.text.endswith("…")
