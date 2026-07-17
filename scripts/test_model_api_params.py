#!/usr/bin/env python3
"""模型接口参数可用性测试脚本（可独立运行，便于后续增改用例）。

覆盖：
1. LLMClientConfig 配置契约（数值范围 / 枚举 / 保留参数）
2. FastAPI Pydantic 请求模型（Create/Update Provider & Model）
3. 模型 CRUD HTTP 往返（TestClient，临时配置，不污染真实 llm_clients.json）
4. chat_protocol / request_params / extra_body 可用性

用法：
  uv run python scripts/test_model_api_params.py
  uv run python scripts/test_model_api_params.py --http-only
  uv run python scripts/test_model_api_params.py --schema-only
  uv run python scripts/test_model_api_params.py -q
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

# 保证从仓库根目录可导入
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


@dataclass
class CaseResult:
    name: str
    ok: bool
    detail: str = ""


@dataclass
class SuiteReport:
    title: str
    results: list[CaseResult] = field(default_factory=list)

    @property
    def passed(self) -> int:
        return sum(1 for r in self.results if r.ok)

    @property
    def failed(self) -> int:
        return sum(1 for r in self.results if not r.ok)


def _expect_ok(name: str, fn: Callable[[], Any]) -> CaseResult:
    try:
        fn()
        return CaseResult(name=name, ok=True)
    except Exception as exc:  # noqa: BLE001 — 脚本汇总所有失败
        return CaseResult(name=name, ok=False, detail=f"{type(exc).__name__}: {exc}")


def _expect_fail(name: str, fn: Callable[[], Any], *exc_types: type[BaseException]) -> CaseResult:
    types = exc_types or (Exception,)
    try:
        fn()
        return CaseResult(name=name, ok=False, detail="预期失败但成功了")
    except types:
        return CaseResult(name=name, ok=True)
    except Exception as exc:  # noqa: BLE001
        return CaseResult(
            name=name,
            ok=False,
            detail=f"异常类型不符: {type(exc).__name__}: {exc}",
        )


# ---------------------------------------------------------------------------
# 1) 配置层契约
# ---------------------------------------------------------------------------

def run_config_suite() -> SuiteReport:
    from agent.llm.llm_client import API_TYPES, LLMClientConfig, ModelType
    from agent.llm.protocol import CHAT_PROTOCOLS
    from pydantic import ValidationError

    report = SuiteReport(title="LLMClientConfig 配置契约")

    # 合法边界值
    valid_cases: list[tuple[str, dict[str, Any]]] = [
        ("defaults", {}),
        ("temperature_0", {"temperature": 0}),
        ("temperature_2", {"temperature": 2}),
        ("top_p_0", {"top_p": 0}),
        ("top_p_1", {"top_p": 1}),
        ("max_tokens_0", {"max_tokens": 0}),
        ("context_window_0", {"context_window": 0}),
        ("timeout_small", {"timeout": 0.001}),
        ("freq_penalty_-2", {"frequency_penalty": -2}),
        ("freq_penalty_2", {"frequency_penalty": 2}),
        ("presence_penalty_-2", {"presence_penalty": -2}),
        ("presence_penalty_2", {"presence_penalty": 2}),
        ("vision_base64", {"vision_format": "base64"}),
        ("vision_url", {"vision_format": "url"}),
        ("vision_both", {"vision_format": "both"}),
        ("request_params_ok", {"request_params": {"api_version": "2025-01-01"}}),
        ("extra_body_ok", {"extra_body": {"custom": True}}),
        ("supports_flags", {
            "supports_vision": True,
            "supports_tools": False,
            "supports_reasoning": True,
        }),
    ]
    for protocol in sorted(CHAT_PROTOCOLS):
        valid_cases.append((f"chat_protocol_{protocol}", {"chat_protocol": protocol}))
    for api_type in API_TYPES:
        valid_cases.append((f"api_type_{api_type}", {"api_type": api_type, "model": "m"}))
    for mt in ModelType:
        valid_cases.append((f"model_types_{mt.value}", {"model_types": [mt.value]}))

    for name, kwargs in valid_cases:
        report.results.append(_expect_ok(
            f"accept:{name}",
            lambda kw=kwargs: LLMClientConfig(**kw),
        ))

    # 非法值
    invalid_cases: list[tuple[str, dict[str, Any]]] = [
        ("temperature_high", {"temperature": 2.1}),
        ("temperature_low", {"temperature": -0.1}),
        ("top_p_high", {"top_p": 1.1}),
        ("top_p_low", {"top_p": -0.1}),
        ("max_tokens_neg", {"max_tokens": -1}),
        ("context_window_neg", {"context_window": -1}),
        ("timeout_zero", {"timeout": 0}),
        ("timeout_neg", {"timeout": -1}),
        ("api_type_unknown", {"api_type": "unknown"}),
        ("vision_format_bad", {"vision_format": "binary"}),
        ("chat_protocol_bad", {"chat_protocol": "websocket"}),
        ("model_types_bad", {"model_types": ["invalid"]}),
        ("request_params_reserved_model", {"request_params": {"model": "x"}}),
        ("request_params_reserved_messages", {"request_params": {"messages": []}}),
        ("request_params_reserved_stream", {"request_params": {"stream": True}}),
        ("request_params_not_object", {"request_params": []}),  # type: ignore[dict-item]
        ("extra_body_not_object", {"extra_body": "x"}),  # type: ignore[dict-item]
    ]
    for name, kwargs in invalid_cases:
        report.results.append(_expect_fail(
            f"reject:{name}",
            lambda kw=kwargs: LLMClientConfig(**kw),
            ValueError,
            TypeError,
        ))

    # to_dict / to_model_dict 字段完整性
    def _serde_roundtrip() -> None:
        cfg = LLMClientConfig(
            name="demo",
            model="gpt-4o",
            chat_protocol="responses",
            request_params={"api_version": "v1"},
            extra_body={"x": 1},
            context_window=128000,
        )
        d = cfg.to_dict()
        m = cfg.to_model_dict()
        for key in (
            "chat_protocol", "request_params", "extra_body",
            "temperature", "top_p", "max_tokens", "timeout",
            "supports_vision", "supports_tools", "supports_reasoning",
            "vision_format", "context_window",
        ):
            assert key in d, f"to_dict 缺少 {key}"
            assert key in m, f"to_model_dict 缺少 {key}"
        restored = LLMClientConfig.from_dict(d)
        assert restored.chat_protocol == "responses"
        assert restored.request_params == {"api_version": "v1"}

    report.results.append(_expect_ok("serde_roundtrip", _serde_roundtrip))

    # API schema 与 config 对齐的非法范围（附带 ValidationError）
    from web.routers.models import CreateModelReq, UpdateModelReq

    def _schema_reject() -> None:
        try:
            UpdateModelReq(temperature=3)
            raise AssertionError("UpdateModelReq 应拒绝 temperature=3")
        except ValidationError:
            pass
        try:
            CreateModelReq(id="m", timeout=0)
            raise AssertionError("CreateModelReq 应拒绝 timeout=0")
        except ValidationError:
            pass
        try:
            CreateModelReq(id="m", chat_protocol="bad")  # type: ignore[arg-type]
            raise AssertionError("CreateModelReq 应拒绝 chat_protocol=bad")
        except ValidationError:
            pass
        try:
            CreateModelReq(id="m", request_params={"model": "other"})
            raise AssertionError("CreateModelReq 应拒绝保留 request_params")
        except ValidationError:
            pass

    report.results.append(_expect_ok("api_schema_rejects", _schema_reject))
    return report


# ---------------------------------------------------------------------------
# 2) API Schema 合法参数矩阵
# ---------------------------------------------------------------------------

def run_schema_suite() -> SuiteReport:
    from web.routers.models import (
        CreateModelReq,
        CreateProviderReq,
        UpdateModelReq,
        UpdateProviderReq,
        _normalize_model_params,
        _serialize_model_config,
    )

    report = SuiteReport(title="FastAPI Schema 参数可用性")

    report.results.append(_expect_ok(
        "CreateProviderReq_openai",
        lambda: CreateProviderReq(
            id="p1", name="P", base_url="https://api.openai.com/v1",
            api_key="sk-test", api_type="openai", proxy_url="",
        ),
    ))
    report.results.append(_expect_ok(
        "UpdateProviderReq_partial",
        lambda: UpdateProviderReq(name="N", proxy_url="127.0.0.1:7890"),
    ))

    create_payload = {
        "id": "m1",
        "model": "gpt-4o",
        "model_types": ["chat"],
        "temperature": 0.4,
        "top_p": 0.9,
        "max_tokens": 8192,
        "frequency_penalty": 0.1,
        "presence_penalty": -0.1,
        "timeout": 60.0,
        "context_window": 128000,
        "supports_vision": True,
        "supports_tools": True,
        "vision_format": "both",
        "supports_reasoning": False,
        "chat_protocol": "auto",
        "request_params": {"service_tier": "auto"},
        "extra_body": {"foo": "bar"},
    }
    report.results.append(_expect_ok(
        "CreateModelReq_full",
        lambda: CreateModelReq(**create_payload),
    ))

    for protocol in ("chat_completions", "responses", "auto"):
        report.results.append(_expect_ok(
            f"CreateModelReq_protocol_{protocol}",
            lambda p=protocol: CreateModelReq(id="x", model="m", chat_protocol=p),
        ))

    report.results.append(_expect_ok(
        "UpdateModelReq_all_optional",
        lambda: UpdateModelReq(
            model="gpt-4.1",
            temperature=1.0,
            top_p=0.5,
            max_tokens=100,
            frequency_penalty=0,
            presence_penalty=0,
            timeout=30,
            context_window=8000,
            supports_vision=False,
            supports_tools=True,
            vision_format="url",
            supports_reasoning=True,
            chat_protocol="responses",
            request_params={"api_version": "2024-10-01"},
            extra_body={"n": 1},
        ),
    ))

    def _normalize_legacy() -> None:
        req = CreateModelReq(
            id="legacy",
            model="m",
            request_params={"k": 1},
            extra_body={"a": 1},
            extra_params={"b": 2},
        )
        normalized = _normalize_model_params(req)
        assert normalized["request_params"] == {"k": 1}
        assert normalized["extra_body"] == {"b": 2, "a": 1}
        assert normalized["extra_params"] == {}
        assert normalized.get("chat_protocol") == "chat_completions"

    report.results.append(_expect_ok("normalize_legacy_extra_params", _normalize_legacy))

    def _serialize_defaults() -> None:
        out = _serialize_model_config({
            "id": "m",
            "extra_params": {"legacy": True},
            "extra_body": {"new": True},
        })
        assert out["chat_protocol"] == "chat_completions"
        assert out["extra_body"] == {"legacy": True, "new": True}
        assert "extra_params" not in out

    report.results.append(_expect_ok("serialize_defaults_chat_protocol", _serialize_defaults))
    return report


# ---------------------------------------------------------------------------
# 3) HTTP 往返（临时配置目录）
# ---------------------------------------------------------------------------

def run_http_suite() -> SuiteReport:
    from fastapi.testclient import TestClient

    report = SuiteReport(title="HTTP /api/models 参数往返")

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        llm_cfg = tmp_path / "llm_clients.json"
        webui_cfg = tmp_path / "webui.json"
        llm_cfg.write_text(json.dumps({
            "providers": [],
            "type_priorities": {},
            "default_chat": "",
        }), encoding="utf-8")
        webui_cfg.write_text(json.dumps({
            "auth": {"password": "", "api_keys": []},
            "server": {"host": "127.0.0.1", "port": 8092},
        }), encoding="utf-8")

        # 在导入 create_app 前注入路径与空管理器
        import agent.llm.llm_manager as mgr_mod
        from agent.llm.llm_manager import LLMManager
        from core.path import ConfigPaths

        old_llm = ConfigPaths.LLM_CLIENTS
        old_webui = ConfigPaths.WEBUI_CONFIG
        ConfigPaths.LLM_CLIENTS = str(llm_cfg)
        ConfigPaths.WEBUI_CONFIG = str(webui_cfg)

        # 重置单例，指向临时配置
        mgr_mod._manager = None  # type: ignore[attr-defined]
        manager = LLMManager(config_path=str(llm_cfg))
        mgr_mod._manager = manager  # type: ignore[attr-defined]

        # ModelService / routers 通过 get_llm_manager 取实例
        import services.model as model_svc_mod
        import web.routers.models as models_router

        try:
            from web.server import create_app
            app = create_app()
            # 确保路由层使用同一临时 manager
            models_router._svc = model_svc_mod.ModelService()
            client = TestClient(app)

            def _step(name: str, fn: Callable[[], None]) -> None:
                report.results.append(_expect_ok(name, fn))

            def create_provider() -> None:
                r = client.post("/api/models/providers", json={
                    "id": "probe_provider",
                    "name": "Probe",
                    "base_url": "https://api.openai.com/v1",
                    "api_key": "sk-real-secret-key-for-mask-test",
                    "api_type": "openai",
                    "proxy_url": "",
                })
                assert r.status_code == 200, r.text

            def list_providers_masks_key() -> None:
                r = client.get("/api/models/providers")
                assert r.status_code == 200, r.text
                data = r.json()
                assert data, "应有供应商"
                key = data[0]["api_key"]
                assert "sk-real-secret-key-for-mask-test" not in key
                assert "****" in key or key == ""

            def create_model_full_params() -> None:
                r = client.post("/api/models/providers/probe_provider/models", json={
                    "id": "probe_model",
                    "model": "gpt-4o",
                    "model_types": ["chat"],
                    "temperature": 0.5,
                    "top_p": 0.8,
                    "max_tokens": 4096,
                    "frequency_penalty": 0.0,
                    "presence_penalty": 0.0,
                    "timeout": 90.0,
                    "context_window": 128000,
                    "supports_vision": True,
                    "supports_tools": True,
                    "vision_format": "base64",
                    "supports_reasoning": False,
                    "chat_protocol": "auto",
                    "request_params": {"service_tier": "auto"},
                    "extra_body": {"demo": True},
                })
                assert r.status_code == 200, r.text

            def get_model_echoes_params() -> None:
                r = client.get("/api/models/probe_model")
                assert r.status_code == 200, r.text
                body = r.json()
                assert body["chat_protocol"] == "auto"
                assert body["request_params"] == {"service_tier": "auto"}
                assert body["extra_body"] == {"demo": True}
                assert body["temperature"] == 0.5
                assert body["context_window"] == 128000
                assert "sk-real-secret-key-for-mask-test" not in r.text

            def update_model_protocol_and_params() -> None:
                r = client.put("/api/models/probe_model", json={
                    "chat_protocol": "responses",
                    "temperature": 0.2,
                    "request_params": {"api_version": "2025-01-01"},
                    "extra_body": {"n": 1},
                })
                assert r.status_code == 200, r.text
                g = client.get("/api/models/probe_model")
                body = g.json()
                assert body["chat_protocol"] == "responses"
                assert body["temperature"] == 0.2
                assert body["request_params"] == {"api_version": "2025-01-01"}
                assert body["extra_body"] == {"n": 1}

            def reject_bad_temperature() -> None:
                r = client.put("/api/models/probe_model", json={"temperature": 9})
                assert r.status_code == 422, r.text

            def reject_reserved_request_params() -> None:
                r = client.put("/api/models/probe_model", json={
                    "request_params": {"model": "hijack"},
                })
                assert r.status_code == 422, r.text

            def reject_bad_chat_protocol() -> None:
                r = client.put("/api/models/probe_model", json={
                    "chat_protocol": "websocket",
                })
                assert r.status_code == 422, r.text

            def update_provider_keeps_masked_key() -> None:
                listed = client.get("/api/models/providers").json()[0]
                masked = listed["api_key"]
                r = client.put("/api/models/providers/probe_provider", json={
                    "api_key": masked,
                    "name": "Probe2",
                })
                assert r.status_code == 200, r.text
                # 真实密钥应仍在配置文件中
                raw = json.loads(llm_cfg.read_text("utf-8"))
                assert raw["providers"][0]["api_key"] == "sk-real-secret-key-for-mask-test"

            def set_default_and_priorities() -> None:
                r = client.put("/api/models/config/default", json={"model_id": "probe_model"})
                assert r.status_code == 200, r.text
                p = client.get("/api/models/priorities")
                assert p.status_code == 200, p.text
                assert "probe_model" in [x["id"] for x in p.json().get("chat", [])]

            def model_info_endpoint() -> None:
                r = client.post("/api/models/model-info", json={
                    "model": "gpt-4o",
                    "api_type": "openai",
                })
                assert r.status_code == 200, r.text

            def cleanup_delete() -> None:
                r = client.delete("/api/models/probe_model")
                assert r.status_code == 200, r.text
                r2 = client.delete("/api/models/providers/probe_provider")
                assert r2.status_code == 200, r2.text

            _step("POST /providers", create_provider)
            _step("GET /providers masks api_key", list_providers_masks_key)
            _step("POST /providers/{id}/models full params", create_model_full_params)
            _step("GET /{model_id} echoes params", get_model_echoes_params)
            _step("PUT /{model_id} chat_protocol+params", update_model_protocol_and_params)
            _step("PUT reject temperature", reject_bad_temperature)
            _step("PUT reject reserved request_params", reject_reserved_request_params)
            _step("PUT reject bad chat_protocol", reject_bad_chat_protocol)
            _step("PUT /providers keep masked key", update_provider_keeps_masked_key)
            _step("PUT default + GET priorities", set_default_and_priorities)
            _step("POST /model-info", model_info_endpoint)
            _step("DELETE model+provider", cleanup_delete)
        finally:
            ConfigPaths.LLM_CLIENTS = old_llm
            ConfigPaths.WEBUI_CONFIG = old_webui
            mgr_mod._manager = None  # type: ignore[attr-defined]

    return report


# ---------------------------------------------------------------------------
# 输出与入口
# ---------------------------------------------------------------------------

def _print_report(report: SuiteReport, *, quiet: bool) -> None:
    status = "PASS" if report.failed == 0 else "FAIL"
    print(f"\n== [{status}] {report.title}  ({report.passed}/{len(report.results)}) ==")
    for item in report.results:
        if quiet and item.ok:
            continue
        mark = "OK" if item.ok else "FAIL"
        line = f"  [{mark}] {item.name}"
        if item.detail:
            line += f"  -- {item.detail}"
        print(line)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="模型接口参数可用性测试")
    parser.add_argument("--schema-only", action="store_true", help="仅跑配置/Schema 契约")
    parser.add_argument("--http-only", action="store_true", help="仅跑 HTTP 往返")
    parser.add_argument("-q", "--quiet", action="store_true", help="只显示失败项")
    args = parser.parse_args(argv)

    suites: list[SuiteReport] = []
    try:
        if args.http_only:
            suites.append(run_http_suite())
        elif args.schema_only:
            suites.append(run_config_suite())
            suites.append(run_schema_suite())
        else:
            suites.append(run_config_suite())
            suites.append(run_schema_suite())
            suites.append(run_http_suite())
    except Exception:
        traceback.print_exc()
        return 2

    total_fail = 0
    total_pass = 0
    for report in suites:
        _print_report(report, quiet=args.quiet)
        total_fail += report.failed
        total_pass += report.passed

    print(f"\n汇总: {total_pass} passed, {total_fail} failed")
    if total_fail:
        print("\n后续改参数时：优先在本脚本的 valid_cases / invalid_cases / HTTP _step 中增改用例。")
        return 1
    print("全部模型接口参数可用性检查通过。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
