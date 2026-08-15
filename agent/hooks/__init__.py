"""用户 hook 事件面 — 工具调用/回复完成事件的用户脚本扩展点（对齐 dsh hooks 桥）。

让用户不改 Python 就能自定义行为：``config/hooks.json`` 按 event 声明 shell
命令，事件触发时串行执行（对齐 Claude Code hooks 习惯）。

事件清单（三个，刻意收窄）：
- ``tool_pre``：工具执行前（可与审批规则叠加，hook 只能收紧不能放宽）
- ``tool_post``：工具执行后（带结果预览）
- ``reply_end``：一次回复完成（带 scope 与摘要）

退出码语义（对齐 Claude Code）：0 = 放行；2 = 阻塞（stderr 作为原因，
仅 tool_pre 有意义）；其他 = 非阻塞错误（WARNING 日志，不影响主流程）。
超时视为非阻塞错误。合并语义对齐 dsh：串行执行，**deny 胜过一切**。

Model Experience：hook 的阻塞结果经工具结果通道返回（tool_error，
cause=PERMISSION），进 tool 消息不进前缀层；空配置时全事件点单次布尔
短路，零开销。
"""

from agent.hooks.runner import (
    HookOutcome,
    HookRegistry,
    HookSpec,
    get_hook_registry,
    hooks_active,
    reload_hooks,
    run_event_hooks,
)

__all__ = [
    "HookOutcome",
    "HookRegistry",
    "HookSpec",
    "get_hook_registry",
    "hooks_active",
    "reload_hooks",
    "run_event_hooks",
]
