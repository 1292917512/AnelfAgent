"""百度智能搜索 API 客户端。

搜索策略：优先使用高性能版（AI 总结 + 搜索），额度用尽自动降级到标准版。
- search_prefer_deep(): 统一入口，自动降级
- search():              标准版 — 纯搜索结果列表（每日 1000 次免费）
- search_with_summary():  高性能版 — AI 总结 + 参考来源（每日 100 次免费）

API 文档:
  标准版:   https://cloud.baidu.com/doc/qianfan-api/s/Hmbu8m06u
  高性能版: https://cloud.baidu.com/doc/qianfan-api/s/wmjqtqr7w
"""

from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, List, Optional

import httpx

from core.log import log

_API_BASE = "https://qianfan.baidubce.com/v2/ai_search"
_STANDARD_URL = f"{_API_BASE}/chat/completions"
_HIGH_PERF_URL = f"{_API_BASE}/web_summary"
_CONFIG_FILE = os.path.join(os.path.dirname(__file__), "config.json")

_config_cache: Optional[Dict[str, Any]] = None


def _load_config() -> Dict[str, Any]:
    """加载配置文件（进程级缓存）。"""
    global _config_cache
    if _config_cache is not None:
        return _config_cache
    try:
        with open(_CONFIG_FILE, encoding="utf-8") as f:
            _config_cache = json.load(f)
    except Exception as e:
        log(f"加载百度搜索配置失败: {e}", "ERROR")
        _config_cache = {}
    return _config_cache


def get_config() -> Dict[str, Any]:
    """获取完整配置（返回副本）。"""
    return dict(_load_config())


def get_api_key() -> str:
    """获取百度搜索 API Key。"""
    return _load_config().get("baidu_api_key", "")


def get_proxy() -> str:
    """获取代理地址（空字符串表示不使用代理）。"""
    return _load_config().get("proxy", "")


def update_config(updates: Dict[str, Any]) -> Dict[str, Any]:
    """更新配置并持久化到文件，返回更新后的完整配置。"""
    global _config_cache
    current = dict(_load_config())
    current.update(updates)
    try:
        with open(_CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(current, f, ensure_ascii=False, indent=4)
        _config_cache = current
        log("百度搜索配置已更新", tag="Web")
    except Exception as e:
        log(f"保存百度搜索配置失败: {e}", "ERROR")
        raise
    return dict(current)


def reload_config() -> None:
    """强制重新加载配置（清除缓存）。"""
    global _config_cache
    _config_cache = None


def _build_headers() -> Dict[str, str]:
    """构建请求头（含鉴权）。"""
    api_key = get_api_key()
    if not api_key:
        raise RuntimeError("百度搜索 API Key 未配置，请检查 entities/web/config.json")
    return {
        "X-Appbuilder-Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }


def _make_client(timeout: float) -> httpx.Client:
    """创建 httpx 客户端，自动应用 config.json 中的代理，隔离环境变量。"""
    proxy = get_proxy()
    return httpx.Client(
        timeout=timeout,
        trust_env=False,
        **({"proxy": proxy} if proxy else {}),
    )


def _extract_web_refs(data: Dict[str, Any]) -> List[Dict[str, str]]:
    """从 API 响应中提取 web 类型的引用结果。"""
    refs = data.get("references") or []
    results: List[Dict[str, str]] = []
    for ref in refs:
        if ref.get("type") not in ("web", None):
            continue
        results.append({
            "title": ref.get("title", ""),
            "url": ref.get("url", ""),
            "snippet": (ref.get("content") or ref.get("snippet") or "")[:300],
            "date": ref.get("date", ""),
            "website": ref.get("website", ""),
        })
    return results


def search(
    query: str,
    max_results: int = 10,
    search_recency: Optional[str] = None,
) -> List[Dict[str, str]]:
    """标准版搜索 — 获取搜索结果列表。

    Args:
        query: 搜索关键词
        max_results: 最大结果数 (1-20)
        search_recency: 时间过滤 (week/month/semiyear/year)

    Returns:
        [{title, url, snippet, date, website}, ...]
    """
    headers = _build_headers()
    body: Dict[str, Any] = {
        "messages": [{"role": "user", "content": query}],
        "stream": False,
        "search_source": "baidu_search_v2",
        "resource_type_filter": [
            {"type": "web", "top_k": min(max(1, max_results), 20)},
        ],
    }
    if search_recency:
        body["search_recency_filter"] = search_recency

    with _make_client(20.0) as client:
        resp = client.post(_STANDARD_URL, json=body, headers=headers)
        resp.raise_for_status()
        data = resp.json()

    if data.get("code"):
        raise RuntimeError(
            f"百度搜索 API 错误 ({data['code']}): {data.get('message', '')}"
        )

    return _extract_web_refs(data)


def search_with_summary(
    query: str,
    max_results: int = 10,
) -> Dict[str, Any]:
    """高性能版搜索 — 获取 AI 总结 + 搜索结果。

    Args:
        query: 搜索关键词
        max_results: 最大结果数 (1-50)

    Returns:
        {"summary": str, "references": [{title, url, snippet, date, website}, ...]}
    """
    headers = _build_headers()
    body: Dict[str, Any] = {
        "messages": [{"role": "user", "content": query}],
        "instruction": "简洁准确地总结搜索结果，保留关键信息和数据。",
        "stream": False,
        "resource_type_filter": [
            {"type": "web", "top_k": min(max(1, max_results), 50)},
        ],
    }

    with _make_client(30.0) as client:
        resp = client.post(_HIGH_PERF_URL, json=body, headers=headers)
        resp.raise_for_status()
        data = resp.json()

    if data.get("code"):
        raise RuntimeError(
            f"百度搜索 API 错误 ({data['code']}): {data.get('message', '')}"
        )

    summary = ""
    choices = data.get("choices") or []
    if choices:
        msg = choices[0].get("message") or {}
        summary = msg.get("content", "")

    return {"summary": summary, "references": _extract_web_refs(data)}


# ──────────────────────────────────────────────────────────────────────────────
# 自动降级：高性能版 → 标准版
# ──────────────────────────────────────────────────────────────────────────────

_high_perf_disabled_until: float = 0.0


def _is_quota_error(exc: Exception) -> bool:
    """判断异常是否为额度耗尽。"""
    msg = str(exc).lower()
    if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 429:
        return True
    return any(kw in msg for kw in ("quota", "limit", "频率", "额度", "超限", "rate"))


def search_prefer_deep(
    query: str,
    max_results: int = 10,
    search_recency: Optional[str] = None,
) -> Dict[str, Any]:
    """统一搜索入口：优先高性能版，额度用尽自动降级到标准版。

    Returns:
        {"summary": str | None, "references": [{title, url, snippet, ...}, ...]}
    """
    global _high_perf_disabled_until

    if time.time() >= _high_perf_disabled_until:
        try:
            return search_with_summary(query, min(max_results, 50))
        except Exception as e:
            if _is_quota_error(e):
                _high_perf_disabled_until = time.time() + 3600
                log("高性能版额度用尽，1 小时内降级到标准版", "WARNING", tag="Web")
            else:
                log(f"高性能版搜索失败，降级到标准版: {e}", "WARNING", tag="Web")

    refs = search(query, min(max_results, 20), search_recency)
    return {"summary": None, "references": refs}
