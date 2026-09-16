"""用户话题指令（user_directives.py）单元测试：抽取 / TTL / 清扫 / 注入块。"""

from __future__ import annotations

import time

import pytest

from agent.memory import user_directives as ud


@pytest.fixture
def directives_file(tmp_path, monkeypatch):
    """指令存储重定向到临时文件（单用例隔离）。"""
    target = tmp_path / "user_directives.json"
    monkeypatch.setattr(
        "agent.memory.user_directives._directives_path", lambda: target,
    )
    return target


class TestExtraction:
    def test_zh_patterns(self):
        assert ud.extract_directives("别再提加班的事了") == ["加班的事"]
        assert ud.extract_directives("不要再提这个了，好吗") == ["这个"]
        assert "前任" in ud.extract_directives("别提前任，我不想聊")

    def test_en_pattern(self):
        terms = ud.extract_directives("Stop talking about the breakup, please")
        assert terms == ["the breakup"]

    def test_no_directive_in_normal_text(self):
        assert ud.extract_directives("今天天气不错，我们去爬山吧") == []
        assert ud.extract_directives("别担心，我没问题的") == []

    def test_term_length_gate(self):
        # 捕获组清洗后不足 2 字 → 丢弃
        assert ud.extract_directives("别提 a 了") == []


class TestObserveAndTtl:
    async def test_register_and_refresh(self, directives_file):
        scope = "user_qq:123"
        assert await ud.observe_message(scope, "别再提上班的事") == ["上班的事"]
        assert ud.active_terms(scope) == ["上班的事"]
        # 再次强调：hit_count +1 → TTL 增长
        await ud.observe_message(scope, "说了别再提上班的事")
        data = ud._load()
        assert data[scope][0]["hit_count"] == 2

    async def test_scope_isolation(self, directives_file):
        await ud.observe_message("user_qq:1", "别再提游戏")
        await ud.observe_message("group_qq:9", "别再提股票")
        assert ud.active_terms("user_qq:1") == ["游戏"]
        assert ud.active_terms("user_qq:2") == []
        assert ud.active_terms("group_qq:9") == ["股票"]

    async def test_expiry_by_silence(self, directives_file):
        scope = "user_qq:1"
        await ud.observe_message(scope, "别再提考试")
        data = ud._load()
        # 沉默超过 TTL（基准 3 天）→ 失效
        data[scope][0]["last_seen_at"] = time.time() - 4 * 86400
        ud._save(data)
        assert ud.active_terms(scope) == []

    async def test_ttl_grows_with_hits(self, directives_file):
        scope = "user_qq:1"
        for _ in range(5):
            await ud.observe_message(scope, "别再提考试")
        data = ud._load()
        # hit=5 → TTL = min(3×5, 30) = 15 天；沉默 10 天仍生效
        data[scope][0]["last_seen_at"] = time.time() - 10 * 86400
        ud._save(data)
        assert ud.active_terms(scope) == ["考试"]

    async def test_sweep_removes_expired(self, directives_file):
        scope = "user_qq:1"
        await ud.observe_message(scope, "别再提考试")
        data = ud._load()
        data[scope][0]["last_seen_at"] = time.time() - 60 * 86400
        ud._save(data)
        removed = await ud.sweep_expired()
        assert removed == 1
        assert ud._load() == {}


class TestBlockRender:
    async def test_block_contains_terms(self, directives_file):
        await ud.observe_message("user_qq:1", "别再提健身")
        block = ud.build_directives_block("user_qq:1")
        assert block.startswith("[纪律·话题禁令]")
        assert "健身" in block

    def test_empty_scope_returns_empty(self):
        assert ud.build_directives_block("") == ""

    async def test_no_terms_returns_empty(self, directives_file):
        await ud.observe_message("user_qq:1", "今天聊点什么")
        assert ud.build_directives_block("user_qq:1") == ""
