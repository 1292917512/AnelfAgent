"""缓存命中状态行（exec_context 注入）单元测试。"""

from __future__ import annotations

from agent.mind.tools.round_helpers import _cache_status_hint, _ThinkRoundState


def _state(**kwargs) -> _ThinkRoundState:
    base = dict(
        last_prompt_tokens=22819,
        last_total_input_tokens=22819,
        last_cache_read_tokens=22784,
        last_cache_creation_tokens=0,
        last_cache_hit_rate=22784 / 22819,
        last_cache_observable=True,
    )
    base.update(kwargs)
    return _ThinkRoundState(**base)


class TestCacheStatusHint:
    def test_renders_rate_and_real_tokens(self) -> None:
        """有真实用量且可观测：渲染命中率与 read/输入 tokens。"""
        hint = _cache_status_hint(_state())
        assert hint.startswith("[缓存] 上轮命中 99.8%")
        assert "read 22,784 / 输入 22,819 tokens" in hint

    def test_creation_tokens_shown_when_present(self) -> None:
        """Anthropic 显式缓存写入量非零时附带写入信息。"""
        hint = _cache_status_hint(_state(last_cache_creation_tokens=1024))
        assert "写入 1,024" in hint

    def test_suppressed_without_real_usage(self) -> None:
        """首轮/压缩后重置轮（last_prompt_tokens=0）不注入。"""
        assert _cache_status_hint(_state(last_prompt_tokens=0)) == ""

    def test_suppressed_when_unobservable(self) -> None:
        """端点不回报缓存字段：静默缺席而非谎报 0%。"""
        assert _cache_status_hint(_state(last_cache_observable=False)) == ""

    def test_excludes_caliber_uses_total_input(self) -> None:
        """prompt 不含缓存口径：输入总量取归一后的 total_input。"""
        hint = _cache_status_hint(_state(
            last_prompt_tokens=300,
            last_total_input_tokens=1000,
            last_cache_read_tokens=700,
            last_cache_hit_rate=0.7,
        ))
        assert "read 700 / 输入 1,000 tokens" in hint

    def test_config_disabled(self, monkeypatch) -> None:
        """配置关闭时不注入。"""
        from core import config as config_mod
        monkeypatch.setattr(config_mod, "get_config_bool", lambda *a, **k: False)
        # round_helpers 内为函数内延迟导入，打点对模块属性同样生效
        assert _cache_status_hint(_state()) == ""
