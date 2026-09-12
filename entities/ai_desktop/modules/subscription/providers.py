"""订阅供应商定义 — 凭据解析、端点与响应归一化。

三家 Coding Plan 供应商（智谱 GLM / MiniMax / Kimi）的用量查询接口差异收敛为
统一快照结构::

    {"plan": 套餐档位 | None,
     "windows": [{"label": "5小时", "remaining_percent": 99.0,
                  "used": …, "limit": …, "remaining": …, "reset_at": 秒级时间戳}]}

另支持按量计费供应商的账户余额查询（DeepSeek）——窗口条目改用
``{"label": "账户余额", "balance": 68.49, "currency": "CNY"}``（无余量百分比
与重置时间）。硅基流动的 /v1/user/info 已 410 弃用且无公开替代、阿里云
百炼需 AccessKey 签名体系，均暂不接入。

凭据链：llm_clients.json 中按 base_url 关键字匹配的首个带 api_key 供应商
（Kimi 的 Coding OAuth access token 亦存于该字段），与 entities/web 的
凭据回退同一口径；本模块自持实现以保持子包自治（删除即整体拔出）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Tuple

from core.log import log

HTTP_TIMEOUT = 15.0

_LOG_TAG = "AI桌面"


@dataclass(frozen=True)
class ProviderSpec:
    """订阅供应商声明（凭据匹配关键字 + 端点 + 响应解析器）。"""

    key: str
    display_name: str
    host_keywords: Tuple[str, ...]
    endpoint: str
    parse: Callable[[Dict[str, Any]], Dict[str, Any]]


def resolve_credential(*host_keywords: str) -> Tuple[str, str]:
    """从 llm_clients.json 按 base_url 关键字匹配首个带凭据的供应商。

    Returns:
        (api_key, provider_id)，未命中返回 ("", "")
    """
    try:
        from core.path import ConfigPaths
        with open(ConfigPaths.LLM_CLIENTS, encoding="utf-8") as f:
            data = json.load(f)
        for provider in data.get("providers", []):
            base_url = str(provider.get("base_url", "")).lower()
            api_key = str(provider.get("api_key", "")).strip()
            if api_key and any(kw in base_url for kw in host_keywords):
                return api_key, str(provider.get("id", ""))
    except Exception as exc:
        log(f"订阅组件读取 LLM 供应商凭据失败: {exc}", "DEBUG", tag=_LOG_TAG)
    return "", ""


# ---- 归一化辅助 ----


def to_float(value: Any) -> Optional[float]:
    """数值字段归一（非法值归 None）。"""
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def ms_to_sec(value: Any) -> Optional[float]:
    """毫秒时间戳转秒（非法值归 None）。"""
    ts = to_float(value)
    return ts / 1000.0 if ts is not None else None


def iso_to_sec(value: Any) -> Optional[float]:
    """ISO 8601 时间串转秒级时间戳（非法值归 None）。"""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _remaining_percent(remaining: Optional[float], limit: Optional[float]) -> Optional[float]:
    """由 剩余/总量 计算剩余百分比（无法计算归 None）。"""
    if remaining is None or not limit:
        return None
    return max(0.0, min(100.0, remaining / limit * 100.0))


# ---- 智谱 GLM Coding Plan ----
# GET https://open.bigmodel.cn/api/monitor/usage/quota/limit
# data.limits[]: {type, unit, number, usage(总量), currentValue(已用),
#                 remaining, percentage(已用%), nextResetTime(ms)}；data.level 为套餐档位

_GLM_UNITS = {2: "分钟", 3: "小时", 5: "天", 6: "周", 7: "月"}


def _glm_window_label(unit: Any, number: Any) -> str:
    """unit+number → 窗口名（如 unit=3,number=5 → 每5小时）。"""
    unit_name = _GLM_UNITS.get(int(unit) if to_float(unit) is not None else -1,
                               f"周期{unit}")
    count = to_float(number) or 1
    if count == 1:
        return f"每{unit_name}"
    return f"每{int(count)}{unit_name}"


def parse_glm(payload: Dict[str, Any]) -> Dict[str, Any]:
    """智谱 GLM quota/limit 响应归一化。"""
    data = payload.get("data") or {}
    windows: List[Dict[str, Any]] = []
    for item in data.get("limits") or []:
        limit = to_float(item.get("usage"))
        used = to_float(item.get("currentValue"))
        remaining = to_float(item.get("remaining"))
        used_pct = to_float(item.get("percentage"))
        windows.append({
            "label": _glm_window_label(item.get("unit"), item.get("number")),
            "used": used,
            "limit": limit,
            "remaining": remaining,
            "remaining_percent": (
                100.0 - used_pct if used_pct is not None
                else _remaining_percent(remaining, limit)
            ),
            "reset_at": ms_to_sec(item.get("nextResetTime")),
        })
    return {"plan": data.get("level") or None, "windows": windows}


# ---- MiniMax Coding Plan ----
# GET https://api.minimaxi.com/v1/token_plan/remains
# model_remains[]（按模型分行，编码用 general 行）：5 小时窗口
# current_interval_remaining_percent + end_time(ms)，周窗口
# current_weekly_remaining_percent + weekly_end_time(ms)


def parse_minimax(payload: Dict[str, Any]) -> Dict[str, Any]:
    """MiniMax token_plan/remains 响应归一化（取 general 模型行）。"""
    rows = payload.get("model_remains") or []
    row = next((r for r in rows if r.get("model_name") == "general"),
               rows[0] if rows else None)
    if row is None:
        raise ValueError("响应中没有套餐用量数据")
    windows: List[Dict[str, Any]] = [
        {
            "label": "每5小时",
            "remaining_percent": to_float(row.get("current_interval_remaining_percent")),
            "reset_at": ms_to_sec(row.get("end_time")),
        },
        {
            "label": "每周",
            "remaining_percent": to_float(row.get("current_weekly_remaining_percent")),
            "reset_at": ms_to_sec(row.get("weekly_end_time")),
        },
    ]
    return {"plan": None, "windows": windows}


# ---- Kimi Coding ----
# GET https://api.kimi.com/coding/v1/usages（Bearer 为 Coding OAuth access token）
# usage: 周期主额度 {limit, used, remaining, resetTime(ISO)}；
# limits[]: 短窗口 {window: {duration, timeUnit}, detail: {limit, used, remaining, resetTime}}

_KIMI_TIME_UNITS = {
    "TIME_UNIT_MINUTE": ("分钟", 60),
    "TIME_UNIT_HOUR": ("小时", 3600),
    "TIME_UNIT_DAY": ("天", 86400),
}


def _kimi_window_label(window: Dict[str, Any]) -> str:
    """duration+timeUnit → 窗口名（300 分钟折算为 5小时）。"""
    duration = to_float(window.get("duration"))
    unit_name, unit_seconds = _KIMI_TIME_UNITS.get(
        str(window.get("timeUnit", "")), ("", 0))
    if duration is None or not unit_seconds:
        return "短窗口"
    if unit_name == "分钟" and duration % 60 == 0:
        return f"每{int(duration // 60)}小时"
    return f"每{int(duration)}{unit_name}"


def _kimi_fill_missing(detail: Dict[str, Any]) -> Dict[str, Any]:
    """补齐 Kimi 窗口的缺省字段（API 在余量为 0 或未使用时省略对应键）。

    remaining 缺失回退 limit - used（配额耗尽时 API 不再返回 remaining），
    used 缺失回退 limit - remaining（新窗口未使用时省略 used）。
    """
    limit = to_float(detail.get("limit"))
    used = to_float(detail.get("used"))
    remaining = to_float(detail.get("remaining"))
    if limit is None:
        return detail
    if remaining is None and used is not None:
        remaining = max(0.0, limit - used)
    if used is None and remaining is not None:
        used = max(0.0, limit - remaining)
    return {"limit": limit, "used": used, "remaining": remaining,
            "resetTime": detail.get("resetTime")}


def parse_kimi(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Kimi usages 响应归一化（周期主额度 + 短窗口）。"""
    windows: List[Dict[str, Any]] = []
    for item in payload.get("limits") or []:
        detail = _kimi_fill_missing(item.get("detail") or {})
        windows.append({
            "label": _kimi_window_label(item.get("window") or {}),
            "used": detail.get("used"),
            "limit": detail.get("limit"),
            "remaining": detail.get("remaining"),
            "remaining_percent": _remaining_percent(
                detail.get("remaining"), detail.get("limit")),
            "reset_at": iso_to_sec(detail.get("resetTime")),
        })
    usage = _kimi_fill_missing(payload.get("usage") or {})
    if usage.get("limit") is not None:
        windows.append({
            "label": "周期总额",
            "used": usage.get("used"),
            "limit": usage.get("limit"),
            "remaining": usage.get("remaining"),
            "remaining_percent": _remaining_percent(
                usage.get("remaining"), usage.get("limit")),
            "reset_at": iso_to_sec(usage.get("resetTime")),
        })
    membership = (payload.get("user") or {}).get("membership") or {}
    level = str(membership.get("level") or "").removeprefix("LEVEL_") or None
    return {"plan": level, "windows": windows}


# ---- DeepSeek（按量计费余额） ----
# GET https://api.deepseek.com/user/balance（标准 Bearer 鉴权）
# balance_infos[]: {currency, total_balance, granted_balance, topped_up_balance}；
# is_available=false 表示余额不足


def parse_deepseek(payload: Dict[str, Any]) -> Dict[str, Any]:
    """DeepSeek 账户余额响应归一化（优先 CNY 条目）。"""
    infos = payload.get("balance_infos") or []
    row = next((i for i in infos if str(i.get("currency", "")).upper() == "CNY"),
               infos[0] if infos else None)
    if row is None:
        raise ValueError("响应中没有余额数据")
    return {
        "plan": "可用" if payload.get("is_available") else "余额不足",
        "windows": [{
            "label": "账户余额",
            "balance": to_float(row.get("total_balance")),
            "currency": str(row.get("currency", "")).upper(),
        }],
    }


PROVIDERS: Tuple[ProviderSpec, ...] = (
    ProviderSpec(
        key="glm",
        display_name="智谱 GLM",
        host_keywords=("bigmodel.cn", "z.ai"),
        endpoint="https://open.bigmodel.cn/api/monitor/usage/quota/limit",
        parse=parse_glm,
    ),
    ProviderSpec(
        key="minimax",
        display_name="MiniMax",
        host_keywords=("minimaxi.com", "minimax.io"),
        endpoint="https://api.minimaxi.com/v1/token_plan/remains",
        parse=parse_minimax,
    ),
    ProviderSpec(
        key="kimi",
        display_name="Kimi",
        host_keywords=("kimi.com", "moonshot"),
        endpoint="https://api.kimi.com/coding/v1/usages",
        parse=parse_kimi,
    ),
    ProviderSpec(
        key="deepseek",
        display_name="DeepSeek",
        host_keywords=("deepseek.com",),
        endpoint="https://api.deepseek.com/user/balance",
        parse=parse_deepseek,
    ),
)
