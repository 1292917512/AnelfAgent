"""ContextProviderRegistry.get_status 的面板口径测试：跨 scope 最近一次收集聚合。"""

from __future__ import annotations

import pytest

from core.context_provider import (
    ContextProviderRegistry,
    ProviderMeta,
    ProviderSnapshot,
)


@pytest.fixture(autouse=True)
def clean_registry():
    ContextProviderRegistry.reset()
    yield
    ContextProviderRegistry.reset()


def _register(name: str = "demo") -> None:
    async def _provide(scope: str) -> ProviderSnapshot:
        return ProviderSnapshot(content="内容", tokens=120, bytes=60, ready=True)

    ContextProviderRegistry.register(ProviderMeta(name=name, provide_fn=_provide))


class TestGetStatusScopeAggregation:
    async def test_panel_reads_latest_collect_across_scopes(self) -> None:
        """收集按真实 scope 分桶时，无 scope 的面板口径取最近一次收集（而非 "" 桶的零值）。"""
        _register()
        # 模拟真实会话 scope 的收集（Web 面板不带 scope）
        await ContextProviderRegistry._collect_background("user_qq:123", 4000)
        status = ContextProviderRegistry.get_status()
        assert status["current_used"] == 120
        assert status["scope"] == "user_qq:123"
        assert status["collected_at"] > 0
        assert status["peak_used"] == 120
        assert status["providers"][0]["tokens"] == 120

    async def test_panel_picks_most_recent_scope(self) -> None:
        """多个 scope 有收集记录时取最新；峰值取全 scope 历史最大。"""
        _register()
        await ContextProviderRegistry._collect_background("user_qq:111", 4000)
        await ContextProviderRegistry._collect_background("user_webui:web#c1", 4000)
        status = ContextProviderRegistry.get_status()
        assert status["scope"] == "user_webui:web#c1"
        assert status["current_used"] == 120
        assert status["peak_used"] == 120

    async def test_explicit_scope_unaffected(self) -> None:
        """指定 scope 的查询保持按 scope 取值（原有行为）。"""
        _register()
        await ContextProviderRegistry._collect_background("user_qq:123", 4000)
        scoped = ContextProviderRegistry.get_status("user_qq:123")
        assert scoped["current_used"] == 120
        empty = ContextProviderRegistry.get_status("user_qq:nobody")
        assert empty["current_used"] == 0

    def test_no_collects_returns_zero(self) -> None:
        """尚无收集记录：零值且不携带归属。"""
        _register()
        status = ContextProviderRegistry.get_status()
        assert status["current_used"] == 0
        assert status["collected_at"] == 0
        assert status["peak_used"] == 0

    async def test_snapshot_without_tokens_estimated_from_content(self) -> None:
        """快照模式未自报 tokens 时按内容粗估：占用/峰值不再恒 0（ssh/voiceprint 即此形态）。"""
        async def _provide(scope: str) -> ProviderSnapshot:
            return ProviderSnapshot(content="x" * 400, ready=True)

        ContextProviderRegistry.register(ProviderMeta(name="bare", provide_fn=_provide))
        await ContextProviderRegistry._collect_background("user_qq:123", 4000)
        status = ContextProviderRegistry.get_status()
        assert status["current_used"] == 100
        assert status["peak_used"] == 100
        assert status["providers"][0]["tokens"] == 100

    async def test_snapshot_self_reported_tokens_respected(self) -> None:
        """自报 tokens 时不覆盖（精确值优先于粗估）。"""
        _register()  # tokens=120
        await ContextProviderRegistry._collect_background("user_qq:123", 4000)
        status = ContextProviderRegistry.get_status()
        assert status["current_used"] == 120


class TestGroupGating:
    """声明 group 的 provider 随实体启停联动：分组工具全禁用即停止采集与注入。"""

    @pytest.fixture(autouse=True)
    def _tools(self):
        from core.entity import EntityRegistry

        for name in ("cp_g1", "cp_g2"):
            EntityRegistry.register_tool(name=name, func=lambda: "ok", group="cp_group")
        yield
        for name in ("cp_g1", "cp_g2"):
            EntityRegistry.unregister(name)

    def _register_grouped(self, group: str | None = "cp_group", name: str = "grouped") -> None:
        async def _provide(scope: str) -> ProviderSnapshot:
            return ProviderSnapshot(content=f"[{name}] 状态", tokens=10, bytes=20, ready=True)

        ContextProviderRegistry.register(
            ProviderMeta(name=name, provide_fn=_provide, group=group),
        )

    async def test_disabled_group_stops_injection(self) -> None:
        """分组全部工具禁用 → 快照不采集不注入；重新启用自动恢复。"""
        from core.entity import EntityRegistry

        self._register_grouped()
        await ContextProviderRegistry._collect_background("s1", 4000)
        assert ContextProviderRegistry._last_snippets["s1"] == ["[grouped] 状态"]

        EntityRegistry.disable_group("cp_group")
        await ContextProviderRegistry._collect_background("s1", 4000)
        assert ContextProviderRegistry._last_snippets["s1"] == []

        EntityRegistry.enable_group("cp_group")
        await ContextProviderRegistry._collect_background("s1", 4000)
        assert ContextProviderRegistry._last_snippets["s1"] == ["[grouped] 状态"]

    async def test_partial_disable_keeps_provider_active(self) -> None:
        """分组内仍有启用工具时（目录中分组仍可见）provider 继续注入。"""
        from core.entity import EntityRegistry

        self._register_grouped()
        EntityRegistry.disable("cp_g1")
        await ContextProviderRegistry._collect_background("s1", 4000)
        assert ContextProviderRegistry._last_snippets["s1"] == ["[grouped] 状态"]

    async def test_ungrouped_provider_always_active(self) -> None:
        """未声明 group 的 provider 视为全局常驻，不随实体启停。"""
        from core.entity import EntityRegistry

        self._register_grouped(group=None, name="global")
        EntityRegistry.disable_group("cp_group")
        await ContextProviderRegistry._collect_background("s1", 4000)
        assert ContextProviderRegistry._last_snippets["s1"] == ["[global] 状态"]

    def test_status_exposes_group_and_active(self) -> None:
        """Web 面板可观测 provider 的归属分组与活动状态。"""
        from core.entity import EntityRegistry

        self._register_grouped()
        status = ContextProviderRegistry.get_status()
        assert status["providers"][0]["group"] == "cp_group"
        assert status["providers"][0]["active"] is True

        EntityRegistry.disable_group("cp_group")
        status = ContextProviderRegistry.get_status()
        assert status["providers"][0]["active"] is False


class TestInjectKeyGating:
    """声明 inject_key 的 provider 受配置开关门控：False 即停止采集与注入。"""

    def _register(self) -> None:
        async def _provide(scope: str) -> ProviderSnapshot:
            return ProviderSnapshot(content="[gated] 状态", tokens=10, bytes=20, ready=True)

        ContextProviderRegistry.register(
            ProviderMeta(
                name="gated", provide_fn=_provide,
                inject_key="gated_context_inject",
            ),
        )

    async def test_switch_off_stops_injection(self) -> None:
        """开关关闭 → 快照不采集不注入；重新打开自动恢复。"""
        from core.config import ConfigManager

        self._register()
        ConfigManager.set("gated_context_inject", True)
        await ContextProviderRegistry._collect_background("s1", 4000)
        assert ContextProviderRegistry._last_snippets["s1"] == ["[gated] 状态"]

        ConfigManager.set("gated_context_inject", False)
        await ContextProviderRegistry._collect_background("s1", 4000)
        assert ContextProviderRegistry._last_snippets["s1"] == []

        ConfigManager.set("gated_context_inject", True)
        await ContextProviderRegistry._collect_background("s1", 4000)
        assert ContextProviderRegistry._last_snippets["s1"] == ["[gated] 状态"]

    def test_switch_defaults_true_when_unset(self) -> None:
        """配置项未设置时默认放行（开关是退出机制而非准入门槛）。"""
        self._register()
        meta = ContextProviderRegistry.get_all()[0]
        assert ContextProviderRegistry._is_active(meta) is True

    def test_status_exposes_inject_key_and_active(self) -> None:
        """Web 面板可观测 provider 的注入开关键与活动状态。"""
        from core.config import ConfigManager

        self._register()
        ConfigManager.set("gated_context_inject", False)
        provider = ContextProviderRegistry.get_status()["providers"][0]
        assert provider["inject_key"] == "gated_context_inject"
        assert provider["active"] is False


class TestLastContentExposure:
    """每个 provider 最近一次注入的正文被记录并暴露给 Web 面板（跨 scope 取最新）。"""

    async def test_last_content_recorded_and_updated(self) -> None:
        contents = iter(["[demo] 第一次", "[demo] 第二次"])

        async def _provide(scope: str) -> ProviderSnapshot:
            return ProviderSnapshot(content=next(contents), tokens=10, bytes=20)

        ContextProviderRegistry.register(
            ProviderMeta(name="content_demo", provide_fn=_provide),
        )
        await ContextProviderRegistry._collect_background("s1", 4000)
        provider = ContextProviderRegistry.get_status()["providers"][0]
        assert provider["last_content"] == "[demo] 第一次"
        assert provider["last_content_scope"] == "s1"
        assert provider["last_content_at"] > 0

        await ContextProviderRegistry._collect_background("s2", 4000)
        provider = ContextProviderRegistry.get_status()["providers"][0]
        assert provider["last_content"] == "[demo] 第二次"
        assert provider["last_content_scope"] == "s2"

    async def test_no_injection_no_content(self) -> None:
        async def _provide(scope: str) -> None:
            return None

        ContextProviderRegistry.register(
            ProviderMeta(name="empty_demo", provide_fn=_provide),
        )
        await ContextProviderRegistry._collect_background("s1", 4000)
        provider = ContextProviderRegistry.get_status()["providers"][0]
        assert provider["last_content"] is None
        assert provider["last_content_at"] == 0.0


class TestFreshCollect:
    """collect 的实时语义：首次/过期即内联重收，新鲜窗口内复用缓存。"""

    async def test_first_collect_returns_content_immediately(self) -> None:
        """首次调用即返回内容（不再滞后一轮返回空）。"""
        self._register_counter()
        snippets, _ = await ContextProviderRegistry.collect("s1")
        assert snippets == ["[fresh] 第1次"]

    async def test_within_ttl_reuses_cache(self) -> None:
        """新鲜度窗口内复用缓存，不重复调用 provider。"""
        counter = self._register_counter()
        await ContextProviderRegistry.collect("s1")
        snippets, _ = await ContextProviderRegistry.collect("s1")
        assert snippets == ["[fresh] 第1次"]
        assert counter[0] == 1

    async def test_stale_cache_refreshes_inline(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """缓存超过新鲜度阈值即内联重收，返回最新内容。"""
        monkeypatch.setattr(
            "core.context_provider._COLLECT_FRESH_SECONDS", 0.0,
        )
        counter = self._register_counter()
        await ContextProviderRegistry.collect("s1")
        snippets, _ = await ContextProviderRegistry.collect("s1")
        assert snippets == ["[fresh] 第2次"]
        assert counter[0] == 2

    async def test_providers_collected_concurrently(self) -> None:
        """多 provider 并发采集：两个各 0.4s 的 provider 总耗时应 < 0.8s。"""
        import asyncio
        import time

        async def _slow(scope: str) -> ProviderSnapshot:
            await asyncio.sleep(0.4)
            return ProviderSnapshot(content="慢速内容")

        ContextProviderRegistry.register(
            ProviderMeta(name="slow_a", provide_fn=_slow),
        )
        ContextProviderRegistry.register(
            ProviderMeta(name="slow_b", provide_fn=_slow),
        )
        start = time.perf_counter()
        snippets, _ = await ContextProviderRegistry.collect("s1")
        elapsed = time.perf_counter() - start
        assert snippets == ["慢速内容", "慢速内容"]
        assert elapsed < 0.8

    def _register_counter(self) -> list:
        counter = [0]

        async def _provide(scope: str) -> ProviderSnapshot:
            counter[0] += 1
            return ProviderSnapshot(content=f"[fresh] 第{counter[0]}次")

        ContextProviderRegistry.register(
            ProviderMeta(name="fresh", provide_fn=_provide),
        )
        return counter
