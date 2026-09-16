"""操作态势注入 — 可用操作 + 注释 + MCP 状态的动态上下文。

让 AI 每轮知道"现在有哪些操作可用、各自的语义注释"（注册的 MCP 操作
名称是人类/AI 起的语义名，不注入则无从知晓）；桌面动作只占一行——
参数契约在工具 schema 里，这里不重复。无操作且无已连接 MCP 时零注入。
"""

from __future__ import annotations

import time

from entities._sdk import context_provider

from . import executor, framework


@context_provider(
    name="operation", priority=33, max_tokens=300,
    group="operation", inject_key="operation_context_inject",
)
class OperationProvider:
    """注入操作态势（可用操作 + 注释 + MCP server 概览）。"""

    def __init__(self) -> None:
        self.last_inject_at: float = 0.0

    async def provide(self, scope: str) -> str:
        from . import desktop as desktop_exec

        snap = framework.snapshot_for_context()
        lines: list[str] = []
        if snap["desktop"]:
            if desktop_exec.runtime_ready():
                lines.append(
                    "桌面操控可用（desktop_act 执行；先用 vision_look 看屏定位坐标，"
                    "鼠标猛移屏幕左上角可物理中止）。"
                )
            else:
                lines.append(
                    "桌面操控未就绪（pyautogui 未安装，install_python_packages 可补装）——"
                    "注册的 MCP 操作不受影响。"
                )
        mcp_ops = snap["mcp"]
        if mcp_ops:
            lines.append("注册的操作（execute_operation 按注释语义调用）：")
            for op in mcp_ops:
                note = f"：{op['annotation']}" if op.get("annotation") else ""
                lines.append(f"- {op['id']}{note}（server={op['server']}）")
        gateway = executor.mcp_gateway()
        if gateway is not None:
            try:
                connected = gateway.connected_servers()
                if connected:
                    summary = "、".join(
                        f"{name}({len(tools)} 工具)" for name, tools in connected.items()
                    )
                    lines.append(
                        f"另有未注册的 MCP 工具可直接使用：{summary}"
                        "（activate_tool_group(\"mcp:<server>\") 激活后调用）。"
                    )
            except Exception:
                pass
        if not lines:
            return ""
        self.last_inject_at = time.time()
        return "[系统注入·操作态势] " + "\n".join(lines)
