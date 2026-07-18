"""Prompt 分层缓存（agent.mind.prompt_layers）单元测试。"""

from __future__ import annotations

from agent.mind.prompt_layers import (
    LAYER_CONTEXT,
    LAYER_STABLE,
    PromptCacheManager,
)


class TestPromptCacheManager:
    def test_build_on_miss_then_hit(self) -> None:
        mgr = PromptCacheManager()
        calls = {"n": 0}

        def builder() -> str:
            calls["n"] += 1
            return "stable content"

        content1, hit1 = mgr.get_or_build("s1", LAYER_STABLE, "hash1", builder)
        content2, hit2 = mgr.get_or_build("s1", LAYER_STABLE, "hash1", builder)
        assert content1 == "stable content" and not hit1
        assert content2 == "stable content" and hit2
        assert calls["n"] == 1, "哈希一致时不应重复构建"

    def test_hash_change_triggers_rebuild(self) -> None:
        mgr = PromptCacheManager()
        mgr.get_or_build("s1", LAYER_STABLE, "hash1", lambda: "v1")
        content, hit = mgr.get_or_build("s1", LAYER_STABLE, "hash2", lambda: "v2")
        assert content == "v2" and not hit

    def test_scope_isolation(self) -> None:
        mgr = PromptCacheManager()
        mgr.get_or_build("s1", LAYER_STABLE, "h", lambda: "scope1")
        content, hit = mgr.get_or_build("s2", LAYER_STABLE, "h", lambda: "scope2")
        assert content == "scope2" and not hit

    def test_layer_isolation(self) -> None:
        mgr = PromptCacheManager()
        mgr.get_or_build("s1", LAYER_STABLE, "h", lambda: "stable")
        content, hit = mgr.get_or_build("s1", LAYER_CONTEXT, "h", lambda: "context")
        assert content == "context" and not hit

    def test_invalidate_scope(self) -> None:
        mgr = PromptCacheManager()
        mgr.get_or_build("s1", LAYER_STABLE, "h", lambda: "v")
        mgr.invalidate("s1")
        _, hit = mgr.get_or_build("s1", LAYER_STABLE, "h", lambda: "v")
        assert not hit

    def test_invalidate_layer(self) -> None:
        mgr = PromptCacheManager()
        mgr.get_or_build("s1", LAYER_STABLE, "h", lambda: "v")
        mgr.get_or_build("s1", LAYER_CONTEXT, "h", lambda: "c")
        mgr.invalidate("s1", layer=LAYER_STABLE)
        _, hit_stable = mgr.get_or_build("s1", LAYER_STABLE, "h", lambda: "v")
        _, hit_context = mgr.get_or_build("s1", LAYER_CONTEXT, "h", lambda: "c")
        assert not hit_stable and hit_context

    def test_invalidate_all(self) -> None:
        mgr = PromptCacheManager()
        mgr.get_or_build("s1", LAYER_STABLE, "h", lambda: "v")
        mgr.invalidate()
        _, hit = mgr.get_or_build("s1", LAYER_STABLE, "h", lambda: "v")
        assert not hit

    def test_stats(self) -> None:
        mgr = PromptCacheManager()
        mgr.get_or_build("s1", LAYER_STABLE, "h", lambda: "v")
        mgr.get_or_build("s1", LAYER_STABLE, "h", lambda: "v")
        stats = mgr.stats()
        assert stats["hits"] == 1 and stats["misses"] == 1
        assert stats["hit_rate"] == 0.5

    def test_compute_hash_deterministic(self) -> None:
        h1 = PromptCacheManager.compute_hash("a", "b")
        h2 = PromptCacheManager.compute_hash("a", "b")
        h3 = PromptCacheManager.compute_hash("a", "c")
        assert h1 == h2 and h1 != h3
