"""订阅额度组件 — Coding Plan 用量与按量账户余额监控注入。

轮询型组件：后台按 ``refresh_minutes``（默认 30 分钟）轮询订阅供应商
（百炼 Token Plan / 智谱 GLM / MiniMax / Kimi）的用量查询接口与按量供应商（DeepSeek）的
账户余额接口，凭据自动匹配自 llm_clients.json 的供应商配置（base_url
关键字识别，Kimi 的 OAuth access token 同样存于 api_key 字段）。
各平台默认关闭，仅采集显式启用的平台。百炼复用控制台 token，失效后暂停查询。
render 只读缓存快照，每家一行注入文本。

Model Experience:
    模型看到什么 —— 各有凭据供应商一行 ``[订阅] 名称（档位）：窗口余量/重置时间``，
        查询失败时该行如实标注失败原因；未配置、关闭和未登录的平台不注入；
    token 影响 —— 每轮增量注入，每家约 30~60 token；未登录的百炼不注入；
    缓存影响 —— 走 volatile 尾部动态区（ai_desktop provider 聚合），不触碰
        stable/conversation 前缀缓存。
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import httpx

from ...framework import DesktopModule, desktop_module
from .bailian import ConsoleLoginRequired
from .providers import PROVIDERS, ProviderSpec

# 凭据解析结果缓存 TTL（避免调度循环每拍重读 llm_clients.json）
_CREDENTIAL_CACHE_TTL = 60.0


@desktop_module
class SubscriptionModule(DesktopModule):
    """轮询订阅余量与余额，将缓存快照提供给桌面和动态上下文。"""

    key = "subscription"
    display_name = "订阅额度"
    description = "订阅用量与账户余额监控（阿里百炼 / GLM / MiniMax / Kimi / DeepSeek）"
    priority = 30
    refresh_interval = 1800.0
    config_schema = {
        **{
            f"provider_{spec.key}_enabled": {
                "description": f"监控 {spec.display_name} 额度（独立于模型调用开关）",
                "default": False,
            }
            for spec in PROVIDERS
        },
        "refresh_minutes": {
            "description": "订阅额度刷新间隔",
            "default": 30,
            "min": 5,
            "max": 720,
            "step": 5,
            "unit": "分钟",
            "advanced": True,
        },
    }

    def __init__(self) -> None:
        super().__init__()
        # 供应商 key -> 快照 {"plan", "windows", "fetched_at"} / {"error": ...}
        self._data: Dict[str, Dict[str, Any]] = {}
        # 供应商 key -> (api_key, provider_id)；带 TTL 的凭据解析缓存
        self._credentials: Dict[str, Tuple[str, str]] = {}
        self._credentials_at: float = 0.0
        self._refresh_lock = asyncio.Lock()

    # ---- 凭据 ----

    def provider_enabled(self, spec: ProviderSpec) -> bool:
        """只采集明确启用的平台，关闭后立即停止显示和上下文注入。"""
        value = self.get_config(f"provider_{spec.key}_enabled", False)
        return value is True or (isinstance(value, str) and value.lower() == "true")

    def _credential(self, spec: ProviderSpec) -> Tuple[str, str]:
        """解析供应商凭据（60s TTL 缓存，热跟随 LLM 配置修改）。"""
        if time.time() - self._credentials_at > _CREDENTIAL_CACHE_TTL:
            self._credentials = {
                spec.key: spec.credential()
                for spec in PROVIDERS if self.provider_enabled(spec)
            }
            self._credentials_at = time.time()
        return self._credentials.get(spec.key, ("", ""))

    def _active_specs(self) -> List[ProviderSpec]:
        """有凭据的供应商列表（无凭据的供应商不参与采集与注入）。"""
        return [spec for spec in PROVIDERS if self.provider_enabled(spec) and self._credential(spec)[0]]

    # ---- 调度 ----

    def due(self, now: float) -> bool:
        """刷新间隔取配置值（热生效）；有凭据供应商缺数据时立即到期。"""
        try:
            minutes = int(self.get_config("refresh_minutes", 30))
        except (TypeError, ValueError):
            minutes = 30
        interval = max(5, minutes) * 60.0
        if now - self.last_refresh >= interval:
            return True
        return any(spec.key not in self._data for spec in self._active_specs())

    async def refresh(self) -> None:
        """并发采集额度，重叠刷新共用结果，单家失败不影响其他来源。"""
        if self._refresh_lock.locked():
            async with self._refresh_lock:
                return
        async with self._refresh_lock:
            self._credentials_at = 0.0
            specs = self._active_specs()
            results = await asyncio.gather(*(
                asyncio.to_thread(self._fetch_one, spec, self._credential(spec)[0])
                for spec in specs
            ))
            self._data = {spec.key: result for spec, result in zip(specs, results, strict=True)}

    # ---- 渲染 ----

    def render(self) -> Optional[str]:
        """从结构化快照格式化注入文本（每家供应商一行，含失败提示）。"""
        lines: List[str] = []
        for spec in PROVIDERS:
            if not self.provider_enabled(spec):
                continue
            data = self._data.get(spec.key)
            if not data or data.get("needs_auth"):
                continue
            name = spec.display_name
            if data.get("plan"):
                name += f"（{data['plan']}）"
            if "error" in data:
                lines.append(f"[订阅] {name}：查询失败（{data['error']}）")
                continue
            segments = [seg for w in data.get("windows", [])
                        if (seg := self._format_window(w))]
            if segments:
                lines.append(f"[订阅] {name}：" + "；".join(segments))
        return "\n".join(lines) if lines else None

    @staticmethod
    def _format_window(window: Dict[str, Any]) -> str:
        """单窗口 → "每5小时 剩 87/100（重置 09-12 03:37）" 片段。

        余额型窗口（按量计费供应商）→ "账户余额 ¥68.49"（无重置时间）。
        """
        label = str(window.get("label") or "窗口")
        if window.get("balance") is not None:
            currency = str(window.get("currency") or "CNY")
            symbol = {"CNY": "¥", "USD": "$", "EUR": "€"}.get(currency, f"{currency} ")
            return f"{label} {symbol}{window['balance']:.2f}"
        limit = window.get("limit")
        remaining = window.get("remaining")
        pct = window.get("remaining_percent")
        if limit is not None and remaining is not None and limit < 10000:
            quota = f"剩 {remaining:g}/{limit:g}"
        elif pct is not None:
            quota = f"剩 {pct:.0f}%"
        else:
            quota = "余量未知"
        reset = window.get("reset_at")
        if reset:
            quota += f"（重置 {datetime.fromtimestamp(float(reset)).strftime('%m-%d %H:%M')}）"
        return f"{label} {quota}"

    def detail(self) -> Dict[str, Any]:
        """面板展示用的结构化详情（逐供应商窗口数据/凭据来源/错误）。"""
        providers: List[Dict[str, Any]] = []
        for spec in PROVIDERS:
            if not self.provider_enabled(spec):
                continue
            _key, source = self._credentials.get(spec.key, ("", ""))
            data = self._data.get(spec.key)
            entry: Dict[str, Any] = {
                "key": spec.key,
                "name": spec.display_name,
                "source": source or None,
                "auth_type": spec.auth_type,
            }
            if spec.key not in self._credentials:
                entry["state"] = "pending"
            elif not _key or (data and data.get("needs_auth")):
                entry["state"] = "no_credential"
            elif data is None:
                entry["state"] = "pending"
            elif "error" in data:
                entry["state"] = "error"
                entry["error"] = data["error"]
            else:
                entry["state"] = "ok"
                entry["plan"] = data.get("plan")
                entry["windows"] = data.get("windows", [])
                entry["fetched_at"] = data.get("fetched_at")
            providers.append(entry)
        return {
            "providers": providers,
            "refresh_minutes": self.get_config("refresh_minutes", 30),
        }

    @staticmethod
    def _short_error(exc: Exception) -> str:
        """异常收敛为简短错误描述（注入文本与面板共用）。"""
        if isinstance(exc, httpx.HTTPStatusError):
            code = exc.response.status_code
            if code in (401, 403):
                return f"HTTP {code} 凭据失效或权限不足"
            return f"HTTP {code}"
        if isinstance(exc, httpx.TimeoutException):
            return "请求超时"
        return str(exc)[:60]

    def _fetch_one(self, spec: ProviderSpec, credential: str) -> Dict[str, Any]:
        """采集单家额度，并将登录状态和错误收敛为快照。"""
        try:
            revision = spec.auth_revision() if spec.auth_revision else None
            previous = self._data.get(spec.key, {})
            if revision is not None and previous.get("needs_auth") and previous.get("auth_revision") == revision:
                return previous
            snapshot = spec.fetch(credential)
            snapshot["fetched_at"] = time.time()
            return snapshot
        except ConsoleLoginRequired:
            return {"needs_auth": True, **({"auth_revision": revision} if revision is not None else {})}
        except Exception as exc:
            return {"error": self._short_error(exc)}
