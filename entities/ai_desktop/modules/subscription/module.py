"""订阅额度组件 — Coding Plan 供应商用量监控注入。

轮询型组件：后台按 ``refresh_minutes``（默认 30 分钟）轮询三家 Coding Plan
供应商（智谱 GLM / MiniMax / Kimi）的用量查询接口，凭据自动匹配自
llm_clients.json 的供应商配置（base_url 关键字识别，Kimi 的 OAuth access
token 同样存于 api_key 字段）。无凭据的供应商自动跳过，单家失败收敛为
该家 error 条目不扩散。render 只读缓存快照，每家一行注入文本。

Model Experience:
    模型看到什么 —— 各有凭据供应商一行 ``[订阅] 名称（档位）：窗口余量/重置时间``，
        查询失败时该行如实标注失败原因（凭据过期等需 AI 告知主人处理）；
    token 影响 —— 每轮增量注入，每家约 30~60 token，三家全配约百余 token；
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
from .providers import HTTP_TIMEOUT, PROVIDERS, ProviderSpec, resolve_credential

# 凭据解析结果缓存 TTL（避免调度循环每拍重读 llm_clients.json）
_CREDENTIAL_CACHE_TTL = 60.0


@desktop_module
class SubscriptionModule(DesktopModule):
    """注入 Coding Plan 订阅额度状态（5 小时/周窗口余量与重置时间）。"""

    key = "subscription"
    display_name = "订阅额度"
    description = "Coding Plan 订阅用量监控（智谱 GLM / MiniMax / Kimi，凭据取自 LLM 供应商配置）"
    priority = 30
    refresh_interval = 1800.0
    config_schema = {
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

    # ---- 凭据 ----

    def _credential(self, spec: ProviderSpec) -> Tuple[str, str]:
        """解析供应商凭据（60s TTL 缓存，热跟随 LLM 配置修改）。"""
        if time.time() - self._credentials_at > _CREDENTIAL_CACHE_TTL:
            self._credentials = {
                spec.key: resolve_credential(*spec.host_keywords)
                for spec in PROVIDERS
            }
            self._credentials_at = time.time()
        return self._credentials.get(spec.key, ("", ""))

    def _active_specs(self) -> List[ProviderSpec]:
        """有凭据的供应商列表（无凭据的供应商不参与采集与注入）。"""
        return [spec for spec in PROVIDERS if self._credential(spec)[0]]

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
        """后台轮询全部有凭据供应商（网络 I/O 放线程执行，单家失败不扩散）。"""
        specs = self._active_specs()
        if specs:
            results = await asyncio.to_thread(self._fetch_all, specs)
            self._data.update(results)
        # 清理凭据已消失供应商的缓存数据
        active_keys = {spec.key for spec in specs}
        for key in [k for k in self._data if k not in active_keys]:
            del self._data[key]

    # ---- 渲染 ----

    def render(self) -> Optional[str]:
        """从结构化快照格式化注入文本（每家供应商一行，含失败提示）。"""
        lines: List[str] = []
        for spec in self._active_specs():
            data = self._data.get(spec.key)
            if not data:
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
        """单窗口 → "每5小时 剩 87/100（重置 16:37）" 片段。"""
        label = str(window.get("label") or "窗口")
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
            dt = datetime.fromtimestamp(float(reset))
            within_day = float(reset) - time.time() < 24 * 3600
            fmt = "%H:%M" if within_day else "%m-%d"
            quota += f"（重置 {dt.strftime(fmt)}）"
        return f"{label} {quota}"

    def detail(self) -> Dict[str, Any]:
        """面板展示用的结构化详情（逐供应商窗口数据/凭据来源/错误）。"""
        providers: List[Dict[str, Any]] = []
        for spec in PROVIDERS:
            _key, source = self._credential(spec)
            data = self._data.get(spec.key)
            entry: Dict[str, Any] = {
                "key": spec.key,
                "name": spec.display_name,
                "source": source or None,
            }
            if not _key:
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

    # ---- 以下为同步采集实现（经 asyncio.to_thread 调用） ----

    def _fetch_all(self, specs: List[ProviderSpec]) -> Dict[str, Dict[str, Any]]:
        """逐供应商采集（共享连接；单家异常收敛为该家的 error 条目）。"""
        results: Dict[str, Dict[str, Any]] = {}
        with httpx.Client(timeout=HTTP_TIMEOUT, trust_env=False) as client:
            for spec in specs:
                try:
                    results[spec.key] = self._fetch_one(client, spec)
                except Exception as exc:
                    results[spec.key] = {"error": self._short_error(exc)}
        return results

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

    def _fetch_one(self, client: httpx.Client, spec: ProviderSpec) -> Dict[str, Any]:
        """单供应商用量采集与归一化。"""
        api_key, _source = self._credential(spec)
        resp = client.get(
            spec.endpoint,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Accept": "application/json",
            },
        )
        resp.raise_for_status()
        snapshot = spec.parse(resp.json())
        snapshot["fetched_at"] = time.time()
        return snapshot
