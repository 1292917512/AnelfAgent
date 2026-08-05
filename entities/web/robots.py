"""robots.txt 合规检查组件。

基于标准库 urllib.robotparser 实现，per-host TTL 缓存。
语义：401/403 视为站点禁止一切自动抓取；404/其他 4xx/网络错误 fail-open 放行。
"""

from __future__ import annotations

import threading
import time
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

from core.log import log

_ROBOTS_TTL = 3600.0  # robots.txt 缓存 1 小时
_ROBOTS_TIMEOUT = 10.0

# host_key -> (缓存时间戳, parser 或 None, 状态 ok/forbidden/open)
_cache: Dict[str, Tuple[float, Optional[RobotFileParser], str]] = {}
_lock = threading.Lock()


def _load_robots(host_key: str, use_proxy: bool) -> Tuple[Optional[RobotFileParser], str]:
    """抓取并解析 robots.txt，返回 (parser, 状态)。

    状态：ok（解析成功）/ forbidden（401·403，全站禁止）/ open（抓取失败，放行）。
    """
    import httpx

    # 惰性导入避免与 tools.py 循环依赖
    from entities.web.tools import (
        _USER_AGENT,
        _check_ssrf_url,
        _proxy_kwargs,
        _ssrf_protection_enabled,
    )

    robots_url = f"{host_key}/robots.txt"
    try:
        if _ssrf_protection_enabled():
            err = _check_ssrf_url(robots_url)
            if err:
                log(f"robots.txt 目标被 SSRF 拦截（放行）: {err}", "DEBUG", tag="web")
                return None, "open"
        client_kwargs: Dict[str, Any] = {
            "timeout": _ROBOTS_TIMEOUT,
            "follow_redirects": True,
            "headers": {"User-Agent": _USER_AGENT},
            **_proxy_kwargs(use_proxy),
        }
        with httpx.Client(**client_kwargs) as client:
            resp = client.get(robots_url)
    except Exception as e:
        log(f"robots.txt 抓取失败（放行）: {robots_url}: {e}", "DEBUG", tag="web")
        return None, "open"

    if resp.status_code in (401, 403):
        return None, "forbidden"
    if resp.status_code >= 400:
        return None, "open"
    parser = RobotFileParser(robots_url)
    parser.parse(resp.text.splitlines())
    return parser, "ok"


def is_allowed(url: str, user_agent: str = "*", use_proxy: bool = False) -> Tuple[bool, str]:
    """检查目标站点 robots.txt 是否允许抓取指定 URL。

    Args:
        url: 待抓取的页面地址
        user_agent: 用于匹配 robots 规则的 User-Agent，默认 "*" 通配组
        use_proxy: 抓取 robots.txt 时是否使用代理

    Returns:
        (allowed, detail)：allowed 为 False 时 detail 说明禁止原因
    """
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return True, ""
    host_key = f"{parts.scheme}://{parts.netloc}"

    with _lock:
        entry = _cache.get(host_key)
    if entry is not None and time.monotonic() - entry[0] < _ROBOTS_TTL:
        _, parser, status = entry
    else:
        parser, status = _load_robots(host_key, use_proxy)
        with _lock:
            _cache[host_key] = (time.monotonic(), parser, status)

    if status == "forbidden":
        return False, "站点 robots.txt 返回 401/403，视为禁止一切自动抓取"
    if parser is None:
        return True, ""
    if parser.can_fetch(user_agent, url):
        return True, ""
    return False, f"目标站点 robots.txt 禁止抓取其页面 (User-Agent: {user_agent})"


def clear_cache() -> None:
    """清空 robots.txt 缓存（测试与配置变更场景）。"""
    with _lock:
        _cache.clear()
