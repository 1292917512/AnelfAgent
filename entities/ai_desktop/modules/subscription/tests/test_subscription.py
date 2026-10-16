"""订阅额度组件测试：三家供应商响应解析 / 凭据匹配 / 注入渲染（无网络）。"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from typing import Any
from unittest.mock import Mock

import httpx
import pytest

from core.command import CommandResult
from core.config import ConfigManager
from entities.ai_desktop.modules.subscription import bailian
from entities.ai_desktop.modules.subscription import module as subscription_module
from entities.ai_desktop.modules.subscription.module import SubscriptionModule
from entities.ai_desktop.modules.subscription.providers import (
    PROVIDERS,
    ProviderSpec,
    _glm_window_label,
    _kimi_window_label,
    iso_to_sec,
    ms_to_sec,
    parse_deepseek,
    parse_glm,
    parse_kimi,
    parse_minimax,
)


@pytest.fixture(autouse=True)
def enabled_platforms():
    for spec in PROVIDERS:
        ConfigManager.set(SubscriptionModule.full_config_key(f"provider_{spec.key}_enabled"), True)

_GLM_PAYLOAD = {
    "code": 200,
    "data": {
        "limits": [
            {"type": "CREDIT_LIMIT", "unit": 3, "number": 5, "usage": 28000,
             "currentValue": 0, "remaining": 27999, "percentage": 1,
             "nextResetTime": 1789061613921},
            {"type": "CREDIT_LIMIT", "unit": 6, "number": 1, "usage": 140000,
             "currentValue": 80377, "remaining": 59622, "percentage": 57,
             "nextResetTime": 1789112258997},
        ],
        "level": "max",
    },
}

_MINIMAX_PAYLOAD = {
    "model_remains": [{
        "model_name": "general",
        "end_time": 1789056000000,
        "current_interval_remaining_percent": 99,
        "weekly_end_time": 1789315200000,
        "current_weekly_remaining_percent": 100,
    }],
    "base_resp": {"status_code": 0, "status_msg": "success"},
}

_KIMI_PAYLOAD = {
    "user": {"membership": {"level": "LEVEL_ADVANCED"}},
    "usage": {"limit": "100", "used": "93", "remaining": "7",
              "resetTime": "2026-09-12T03:37:59.094762Z"},
    "limits": [{
        "window": {"duration": 300, "timeUnit": "TIME_UNIT_MINUTE"},
        "detail": {"limit": "100", "used": "13", "remaining": "87",
                   "resetTime": "2026-09-10T16:37:59.094762Z"},
    }],
}


class TestTimeHelpers:
    def test_ms_to_sec(self) -> None:
        assert ms_to_sec(1789061613921) == 1789061613.921
        assert ms_to_sec(None) is None
        assert ms_to_sec("abc") is None

    def test_iso_to_sec(self) -> None:
        assert iso_to_sec("2026-09-12T03:37:59Z") is not None
        assert iso_to_sec(None) is None
        assert iso_to_sec("not-a-date") is None


class TestGlmParse:
    def test_windows_and_plan(self) -> None:
        result = parse_glm(_GLM_PAYLOAD)
        assert result["plan"] == "max"
        five_h, weekly = result["windows"]
        assert five_h["label"] == "每5小时"
        assert five_h["remaining_percent"] == 99.0
        assert five_h["limit"] == 28000
        assert five_h["reset_at"] == 1789061613.921
        assert weekly["label"] == "每周"
        assert weekly["remaining_percent"] == 43.0

    def test_window_label_fallback(self) -> None:
        assert _glm_window_label(3, 5) == "每5小时"
        assert _glm_window_label(6, 1) == "每周"
        assert _glm_window_label(7, 1) == "每月"
        assert _glm_window_label(99, 2) == "每2周期99"

    def test_empty_limits(self) -> None:
        assert parse_glm({"data": {}}) == {"plan": None, "windows": []}


class TestMinimaxParse:
    def test_general_row(self) -> None:
        result = parse_minimax(_MINIMAX_PAYLOAD)
        five_h, weekly = result["windows"]
        assert five_h["label"] == "每5小时"
        assert five_h["remaining_percent"] == 99.0
        assert five_h["reset_at"] == 1789056000.0
        assert weekly["label"] == "每周"
        assert weekly["remaining_percent"] == 100.0

    def test_no_rows_raises(self) -> None:
        with pytest.raises(ValueError):
            parse_minimax({"model_remains": []})


class TestKimiParse:
    def test_windows_and_plan(self) -> None:
        result = parse_kimi(_KIMI_PAYLOAD)
        assert result["plan"] == "ADVANCED"
        short, total = result["windows"]
        assert short["label"] == "每5小时"
        assert short["remaining"] == 87.0
        assert short["remaining_percent"] == 87.0
        assert total["label"] == "周期总额"
        assert total["remaining_percent"] == pytest.approx(7.0)

    def test_window_label_units(self) -> None:
        assert _kimi_window_label({"duration": 300, "timeUnit": "TIME_UNIT_MINUTE"}) == "每5小时"
        assert _kimi_window_label({"duration": 45, "timeUnit": "TIME_UNIT_MINUTE"}) == "每45分钟"
        assert _kimi_window_label({"duration": 2, "timeUnit": "TIME_UNIT_HOUR"}) == "每2小时"
        assert _kimi_window_label({}) == "短窗口"


class TestRender:
    def _module_with_data(self) -> SubscriptionModule:
        module = SubscriptionModule()
        module._credentials_at = time.time()
        module._credentials = {"bailian": ("", ""), "glm": ("key", "zhipu"), "minimax": ("", ""),
                               "kimi": ("key", "Kimi")}
        module._data = {
            "glm": parse_glm(_GLM_PAYLOAD) | {"fetched_at": time.time()},
            "kimi": {"error": "HTTP 401 凭据失效或权限不足"},
        }
        return module

    def test_render_lines_per_provider(self) -> None:
        module = self._module_with_data()
        text = module.render()
        assert text is not None
        lines = text.split("\n")
        assert len(lines) == 2
        assert lines[0].startswith("[订阅] 智谱 GLM（max）：")
        assert "每5小时 剩 99%" in lines[0]
        assert "每周 剩 43%" in lines[0]
        assert "重置" in lines[0]
        assert lines[1] == "[订阅] Kimi：查询失败（HTTP 401 凭据失效或权限不足）"

    def test_render_skips_no_credential(self) -> None:
        """无凭据供应商（minimax）不产出注入行。"""
        module = self._module_with_data()
        assert "MiniMax" not in (module.render() or "")

    def test_render_none_without_data(self) -> None:
        module = SubscriptionModule()
        module._credentials_at = time.time()
        module._credentials = {"glm": ("key", "zhipu")}
        assert module.render() is None

    def test_small_limit_shows_counts(self) -> None:
        """小额度（如 Kimi 请求次数）渲染 剩 X/Y 而非百分比。"""
        module = SubscriptionModule()
        module._credentials_at = time.time()
        module._credentials = {"kimi": ("key", "Kimi")}
        module._data = {"kimi": parse_kimi(_KIMI_PAYLOAD) | {"fetched_at": time.time()}}
        text = module.render() or ""
        assert "剩 87/100" in text
        assert "周期总额 剩 7/100" in text


class TestDetail:
    def test_states(self) -> None:
        module = SubscriptionModule()
        module._credentials_at = time.time()
        module._credentials = {"bailian": ("", ""), "glm": ("key", "zhipu"), "minimax": ("", ""),
                               "kimi": ("key", "Kimi"), "deepseek": ("", "")}
        module._data = {
            "glm": parse_glm(_GLM_PAYLOAD) | {"fetched_at": 1.0},
            "kimi": {"error": "请求超时"},
        }
        states = {p["key"]: p["state"] for p in module.detail()["providers"]}
        assert states == {"bailian": "no_credential", "glm": "ok", "minimax": "no_credential",
                          "kimi": "error", "deepseek": "no_credential"}


class TestKimiMissingFields:
    """配额耗尽时 API 省略 remaining / 新窗口省略 used 的回退补齐。"""

    def test_exhausted_quota_derives_remaining(self) -> None:
        result = parse_kimi({
            "usage": {"limit": "100", "used": "100",
                      "resetTime": "2026-09-12T03:37:59Z"},
        })
        total = result["windows"][0]
        assert total["remaining"] == 0.0
        assert total["remaining_percent"] == 0.0

    def test_fresh_window_derives_used(self) -> None:
        result = parse_kimi({
            "limits": [{
                "window": {"duration": 300, "timeUnit": "TIME_UNIT_MINUTE"},
                "detail": {"limit": "100", "remaining": "100",
                           "resetTime": "2026-09-11T02:37:59Z"},
            }],
        })
        short = result["windows"][0]
        assert short["used"] == 0.0
        assert short["remaining_percent"] == 100.0

    def test_no_limit_keeps_unknown(self) -> None:
        result = parse_kimi({"usage": {"used": "5"}})
        assert result["windows"] == []


class TestResetFormat:
    """重置时间统一 MM-DD HH:MM 完整格式。"""

    @pytest.fixture
    def _shanghai_tz(self, monkeypatch: pytest.MonkeyPatch):
        """钉死渲染时区为 +8（fromtimestamp 走固定时区替身，不依赖系统 tzdata）。"""
        from datetime import datetime, timedelta, timezone

        class _FixedTzDatetime(datetime):
            @classmethod
            def fromtimestamp(cls, ts, tz=None):
                return datetime.fromtimestamp(
                    ts, timezone(timedelta(hours=8)))

        import entities.ai_desktop.modules.subscription.module as sub_module
        monkeypatch.setattr(sub_module, "datetime", _FixedTzDatetime)

    def test_format_window_full_datetime(self, _shanghai_tz) -> None:
        window = {"label": "每5小时", "limit": 100.0, "remaining": 87.0,
                  "reset_at": 1789058279.0}
        assert SubscriptionModule._format_window(window) == \
            "每5小时 剩 87/100（重置 09-11 00:37）"


_DEEPSEEK_PAYLOAD = {
    "is_available": True,
    "balance_infos": [
        {"currency": "CNY", "total_balance": "68.49",
         "granted_balance": "0.00", "topped_up_balance": "68.49"},
    ],
}


class TestDeepseekParse:
    def test_balance_extraction(self) -> None:
        result = parse_deepseek(_DEEPSEEK_PAYLOAD)
        assert result["plan"] == "可用"
        window = result["windows"][0]
        assert window["balance"] == 68.49
        assert window["currency"] == "CNY"

    def test_cny_preferred(self) -> None:
        result = parse_deepseek({
            "is_available": True,
            "balance_infos": [
                {"currency": "USD", "total_balance": "1.00"},
                {"currency": "CNY", "total_balance": "68.49"},
            ],
        })
        assert result["windows"][0]["balance"] == 68.49

    def test_unavailable_flag(self) -> None:
        result = parse_deepseek({"is_available": False,
                                 "balance_infos": [{"currency": "CNY", "total_balance": "0"}]})
        assert result["plan"] == "余额不足"
        assert result["windows"][0]["balance"] == 0.0

    def test_no_balance_raises(self) -> None:
        import pytest
        with pytest.raises(ValueError):
            parse_deepseek({"balance_infos": []})


class TestBalanceRender:
    def test_format_window_balance(self) -> None:
        window = {"label": "账户余额", "balance": 68.49, "currency": "CNY"}
        assert SubscriptionModule._format_window(window) == "账户余额 ¥68.49"

    def test_render_line_with_balance(self) -> None:
        module = SubscriptionModule()
        module._credentials_at = time.time()
        module._credentials = {"deepseek": ("key", "11")}
        module._data = {"deepseek": parse_deepseek(_DEEPSEEK_PAYLOAD)
                        | {"fetched_at": time.time()}}
        assert module.render() == "[订阅] DeepSeek（可用）：账户余额 ¥68.49"


class TestBailian:
    @pytest.mark.parametrize(("used", "remaining"), [
        (0, 100), (0.25, 75), (1, 0), (None, None), (True, None),
        ("NaN", None), (float("inf"), None), (-1, None), (100, None),
    ])
    def test_usage_ratio(self, used: Any, remaining: float | None) -> None:
        window = bailian.parse_plan({
            "per1MonthPercentage": used, "per1MonthResetTime": 1789061613921,
        })["windows"][0]
        assert window["remaining_percent"] == remaining
        assert window["reset_at"] == 1789061613.921
        assert bailian.parse_plan({})["windows"][0]["reset_at"] is None

    def test_read_only_cli_query(self, monkeypatch: pytest.MonkeyPatch) -> None:
        command = Mock(return_value=CommandResult(True, json.dumps({"per1MonthPercentage": 0.5}), "", 0))
        monkeypatch.setattr(bailian, "run_command", command)
        result = bailian.fetch_usage("/tools/bl")
        assert result["windows"][0]["remaining_percent"] == 50
        command.assert_called_once_with(
            ["/tools/bl", "usage", "token-plan", "--output", "json", "--timeout", "15", "--quiet"],
            timeout_sec=20, shell=False, expected_returncodes=(3,), env_vars=None,
        )

    @pytest.mark.parametrize(("result", "exception"), [
        (CommandResult(False, "sensitive", "sensitive", 3), bailian.ConsoleLoginRequired),
        (CommandResult(False, "sensitive", "sensitive", None), ValueError),
        (CommandResult(True, "sensitive", "", 0), ValueError),
        (CommandResult(True, '[]', "", 0), ValueError),
        (CommandResult(True, '{"error":"sensitive"}', "", 0), ValueError),
    ])
    def test_safe_failures(
        self, monkeypatch: pytest.MonkeyPatch, result: CommandResult, exception: type[Exception],
    ) -> None:
        monkeypatch.setattr(bailian, "run_command", Mock(return_value=result))
        with pytest.raises(exception) as error:
            bailian.fetch_usage("/tools/bl")
        assert "sensitive" not in str(error.value)

    def test_cli_discovery(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(bailian.shutil, "which", lambda name: "/tools/bailian" if name == "bailian" else None)
        assert bailian.cli_credential() == ("/tools/bailian", "Bailian CLI")
        monkeypatch.setattr(bailian.shutil, "which", lambda name: None)
        assert bailian.cli_credential() == ("", "")

    def test_login_state_is_not_injected(self) -> None:
        module = SubscriptionModule()
        module._credentials_at = time.time()
        module._credentials = {"bailian": ("/tools/bl", "Bailian CLI")}
        module._data = {"bailian": {"needs_auth": True}}
        assert module.render() is None
        entry = module.detail()["providers"][0]
        assert entry["state"] == "no_credential"
        assert entry["auth_type"] == "console"
        assert "/tools/bl" not in json.dumps(entry)
        module._data["bailian"] = bailian.parse_plan({"per1MonthPercentage": 0.25})
        assert module.detail()["providers"][0]["state"] == "ok"
        assert "每月 剩 75%" in (module.render() or "")


async def test_parallel_refresh_isolated_and_coalesced(monkeypatch: pytest.MonkeyPatch) -> None:
    barrier = threading.Barrier(2, timeout=3)

    def collect(_credential: str) -> dict[str, Any]:
        barrier.wait()
        return {"plan": "test", "windows": []}

    def expired(_credential: str) -> dict[str, Any]:
        barrier.wait()
        raise bailian.ConsoleLoginRequired()

    good, login = Mock(side_effect=collect), Mock(side_effect=expired)
    providers = (
        ProviderSpec("good", "Good", lambda: ("key", "source"), good),
        ProviderSpec("login", "Login", lambda: ("cli", "source"), login, "console"),
        ProviderSpec("failed", "Failed", lambda: ("key", "source"), Mock(side_effect=httpx.ReadTimeout("timeout"))),
    )
    monkeypatch.setattr(subscription_module, "PROVIDERS", providers)
    for spec in providers:
        ConfigManager.set(SubscriptionModule.full_config_key(f"provider_{spec.key}_enabled"), True)
    module = SubscriptionModule()
    module._data = {"removed": {"plan": "stale"}}
    await asyncio.gather(module.refresh(), module.refresh())
    good.assert_called_once()
    login.assert_called_once()
    assert set(module._data) == {"good", "login", "failed"}
    assert module._data["good"]["fetched_at"] > 0
    assert module._data["login"] == {"needs_auth": True}
    assert module._data["failed"] == {"error": "请求超时"}


def test_http_provider_keeps_bearer_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    response = httpx.Response(200, json=_GLM_PAYLOAD, request=httpx.Request("GET", "https://example.test"))
    client = Mock()
    client.get.return_value = response
    factory = Mock()
    factory.return_value.__enter__ = Mock(return_value=client)
    factory.return_value.__exit__ = Mock(return_value=False)
    monkeypatch.setattr(subscription_module.httpx, "Client", factory)
    spec = next(spec for spec in PROVIDERS if spec.key == "glm")
    assert spec.fetch("test-key") == parse_glm(_GLM_PAYLOAD)
    assert client.get.call_args.kwargs["headers"]["Authorization"] == "Bearer test-key"
