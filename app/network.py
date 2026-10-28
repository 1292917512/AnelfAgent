"""所选下载源的有界连通性诊断；不改代理、不自动切源、不下载完整安装包。"""

from __future__ import annotations

import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from app.settings import Settings, download_profile


def inspect_network(settings: Settings) -> dict[str, Any]:
    """并发检测索引和分发入口；入口可达不能替代每个实际资源的下载验证。"""
    profile = download_profile(settings)
    targets = {"pypi": profile["pypi"].rstrip("/") + "/packaging/",
               "npm": profile["npm"].rstrip("/") + "/-/ping",
               "node": profile["node"] + "index.json",
               "python": profile["python"].replace("/releases/download", "").rstrip("/") + "/"}

    def probe(item: tuple[str, str]) -> dict[str, Any]:
        name, url = item
        started = time.monotonic()
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "AnelfAgent/1"})
            with urllib.request.urlopen(request, timeout=5) as response:
                if not response.url.startswith("https://"):
                    raise ValueError("Insecure redirect")
                response.read(1)
                ok, detail = 200 <= response.status < 400, f"HTTP {response.status}"
        except (OSError, ValueError) as exc:
            ok, detail = False, str(exc)[:240]
        return {"name": name, "url": url, "ok": ok, "detail": detail,
                "elapsed_ms": round((time.monotonic() - started) * 1000)}

    with ThreadPoolExecutor(max_workers=4, thread_name_prefix="source-check") as pool:
        results = list(pool.map(probe, targets.items()))
    return {"source": "global" if profile["pypi"] == "https://pypi.org/simple" else "china",
            "checked_at": time.time(), "results": results}
