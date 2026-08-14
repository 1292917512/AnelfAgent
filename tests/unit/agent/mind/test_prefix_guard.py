"""前缀稳定性守卫（agent.mind.prefix_guard）单元测试。

覆盖：首次无基线、前缀稳定、逐位置断裂归因、纯追加免疫、链收缩检测、
用途（kind）隔离、reset 重置、fail-open 健壮性。
"""

from __future__ import annotations

from agent.mind.prefix_guard import PrefixGuard


def _msg(layer: str, content: str, role: str = "system") -> dict:
    return {"role": role, "content": content, "_layer": layer}


class TestPrefixGuardBaseline:
    def test_first_call_no_baseline(self) -> None:
        g = PrefixGuard()
        msgs = [_msg("stable", "人设"), _msg("conversation", "你好", role="user")]
        assert g.check("s1", msgs) is None

    def test_identical_prefix_stable(self) -> None:
        g = PrefixGuard()
        msgs = [_msg("stable", "人设"), _msg("summary", "摘要"), _msg("conversation", "你好", role="user")]
        assert g.check("s1", msgs) is None
        # 完全相同的前缀再次调用 → 稳定
        assert g.check("s1", msgs) is None

    def test_unrelated_layers_ignored(self) -> None:
        """非守卫层（volatile/exec_context）变化不触发断裂。"""
        g = PrefixGuard()
        base = [_msg("stable", "人设"), _msg("conversation", "你好", role="user")]
        assert g.check("s1", base) is None
        # 追加 volatile/exec_context 层消息 → 不影响守卫链
        extended = base + [_msg("volatile", "动态内容"), _msg("exec_context", "第2轮")]
        assert g.check("s1", extended) is None


class TestPrefixGuardDrift:
    def test_stable_layer_change_detected(self) -> None:
        g = PrefixGuard()
        assert g.check("s1", [_msg("stable", "人设A")]) is None
        drift = g.check("s1", [_msg("stable", "人设B")])
        assert drift is not None
        assert drift["layer"] == "stable"
        assert drift["broken_at_index"] == 0
        assert drift["prev_hash"] != drift["cur_hash"]

    def test_drift_reports_first_position(self) -> None:
        g = PrefixGuard()
        assert g.check("s1", [_msg("stable", "p"), _msg("summary", "s")]) is None
        # summary 变、stable 未变 → 断裂点在 index 1
        drift = g.check("s1", [_msg("stable", "p"), _msg("summary", "s2")])
        assert drift is not None
        assert drift["broken_at_index"] == 1
        assert drift["layer"] == "summary"

    def test_append_is_stable(self) -> None:
        """conversation 纯追加（新消息）不构成断裂。"""
        g = PrefixGuard()
        assert g.check("s1", [_msg("stable", "p"), _msg("conversation", "m1", role="user")]) is None
        drift = g.check("s1", [
            _msg("stable", "p"),
            _msg("conversation", "m1", role="user"),
            _msg("conversation", "m2", role="assistant"),
        ])
        assert drift is None

    def test_shrunk_chain_detected(self) -> None:
        g = PrefixGuard()
        assert g.check("s1", [_msg("stable", "p"), _msg("conversation", "m1", role="user")]) is None
        # 守卫链收缩（消息被删/压缩）→ 断裂
        drift = g.check("s1", [_msg("stable", "p")])
        assert drift is not None
        assert drift.get("reason") == "guarded_chain_shrunk"


class TestPrefixGuardIsolation:
    def test_scope_isolation(self) -> None:
        g = PrefixGuard()
        assert g.check("s1", [_msg("stable", "A")]) is None
        # 不同 scope 互不干扰：s2 首次调用无基线
        assert g.check("s2", [_msg("stable", "B")]) is None

    def test_kind_isolation(self) -> None:
        """reply 与 reflect 前缀族独立，交替调用不误报。"""
        g = PrefixGuard()
        assert g.check("s1", [_msg("stable", "reply前缀")], kind="reply") is None
        # reflect 族首次调用（不同前缀）→ 无基线不报断裂
        assert g.check("s1", [_msg("stable", "reflect前缀")], kind="reflect") is None
        # 回到 reply 族 → 与 reply 基线一致，稳定
        assert g.check("s1", [_msg("stable", "reply前缀")], kind="reply") is None

    def test_reset_clears_baseline(self) -> None:
        g = PrefixGuard()
        assert g.check("s1", [_msg("stable", "A")]) is None
        g.reset("s1")
        # 重置后内容变化不再报断裂（视为新基线）
        assert g.check("s1", [_msg("stable", "B")]) is None

    def test_reset_all(self) -> None:
        g = PrefixGuard()
        g.check("s1", [_msg("stable", "A")])
        g.check("s2", [_msg("stable", "B")])
        g.reset()
        assert g.check("s1", [_msg("stable", "A2")]) is None
        assert g.check("s2", [_msg("stable", "B2")]) is None


class TestPrefixGuardRobustness:
    def test_non_string_content_no_crash(self) -> None:
        g = PrefixGuard()
        msgs = [{"role": "user", "content": [{"type": "image", "data": "xxx"}], "_layer": "conversation"}]
        assert g.check("s1", msgs) is None
        # 内容变化仍可检测
        msgs2 = [{"role": "user", "content": [{"type": "image", "data": "yyy"}], "_layer": "conversation"}]
        assert g.check("s1", msgs2) is not None

    def test_extra_fields_captured(self) -> None:
        """cache_control 等 extra 字段漂移也被捕捉。"""
        g = PrefixGuard()
        assert g.check("s1", [{**_msg("stable", "p"), "cache_control": {"type": "ephemeral"}}]) is None
        drift = g.check("s1", [_msg("stable", "p")])  # extra 字段消失
        assert drift is not None

    def test_stats_tracked(self) -> None:
        g = PrefixGuard()
        g.check("s1", [_msg("stable", "A")])
        g.check("s1", [_msg("stable", "B")])
        stats = g.stats()
        assert stats["check_count"] == 2
        assert stats["drift_count"] == 1
        assert stats["tracked_scopes"] == 1
