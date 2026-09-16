"""操作核心能力 — 桌面操控与 MCP 操作注册的统一目录、执行与态势注入。

模块划分：
- framework.py：操作注册表（内置桌面动作 + MCP 注册操作）与注释/启停
  持久化（config/operations.json）；
- desktop.py：pyautogui 执行器（可选依赖探测、to_thread 执行、FAILSAFE）；
- executor.py：统一执行入口、执行历史、MCP 网关晚绑定端口（组合根施绑）；
- context.py：操作态势上下文提供者（可用操作 + 注释 + MCP 概览注入）；
- tools.py：AI 工具面（operation 组）。

看屏定位用视觉组既有工具（vision_look），操作只负责"做"。
"""

from core.config import register_configs_safe

register_configs_safe({
    "operation": {
        "operation_enabled": {
            "description": "是否允许执行桌面操控动作（高危总开关；急停可鼠标猛移屏幕左上角）",
            "default": True,
        },
        "operation_context_window_seconds": {
            "description": "操作活跃窗口（秒）：窗口内有操作执行才注入操作态势上下文，"
                           "平时零注入；每次执行滑动续期",
            "default": 600, "unit": "秒", "min": 60, "max": 7200, "advanced": True,
        },
        "operation_desktop_verify": {
            "description": "桌面动作执行后自动看屏验证（联动视觉 screen 源，动作结果附最新"
                           "画面帧；AI 可用 desktop_act 的 verify 参数逐次覆盖）",
            "default": True,
        },
    },
})
