"""从网络 SDK 读取被动状态，不主动探测或修改代理配置。

模型可见：代理访问失败与近期恢复，通过 AI 桌面注入动态上下文。
Token 影响：无异常不注入，最多三条近期状态，约 100–250 token。
缓存影响：仅更新 volatile 桌面快照，不写历史或稳定前缀。
"""

from __future__ import annotations

import time
from typing import Any

from entities._sdk import get_proxy_status

from ...framework import DesktopModule, desktop_module


@desktop_module
class NetworkModule(DesktopModule):
    """展示实际代理请求的近期状态，未观测时不宣称正常或故障。"""

    key = "network"
    display_name = "代理访问"
    description = "实际请求的代理状态，仅被动记录，不进行循环探测"
    priority = 15

    def detail(self) -> dict[str, Any]:
        return {"network": get_proxy_status()}

    def render(self) -> str | None:
        status = get_proxy_status()
        if not status["enabled"]:
            return None
        failed = [row for row in status["observations"] if row["state"] == "failed"]
        recovered = [row for row in status["observations"] if row["recovered"]
                     and row["state"] == "reachable" and time.time() - row["changed_at"] < 120]
        rows = (failed + recovered)[:3]
        if not rows:
            return None
        lines = ["代理访问状态（近期实际请求，目标失败不等于代理整体不可用）："]
        for row in rows:
            result = f"失败 {row['error_code']}" if row["state"] == "failed" else "已恢复连接"
            lines.append(f"{row['proxy']} → {row['target']}：{result}")
        return "\n".join(lines)
