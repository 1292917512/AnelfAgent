"""思维会话上下文管理 — scope 绑定 + 会话令牌 + 会话收尾的统一封装。

一次思维会话（reply / reflect）的生命周期：
1. 进入：绑定对话 scope（工具激活隔离）+ 生成一次性会话令牌（防注入）
2. 进行：think_loop 多轮 LLM 调用
3. 退出：消耗一轮工具分组激活周期 + 复位绑定 + 清理动态工具

reply 与 reflect 共用本管理器，避免散落的 bind/reset 样板代码。
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import TYPE_CHECKING, Iterator

if TYPE_CHECKING:
    from agent.mind.mind import Mind


@contextmanager
def think_session(
        mind: "Mind",
        scope: str,
        *,
        with_token: bool = True,
        clear_dynamic_tools: bool = True,
) -> Iterator[str]:
    """思维会话上下文管理器。

    Args:
        mind: Mind 实例（用于清理 PFC 动态工具）
        scope: 对话 scope（工具激活状态按 scope 隔离）
        with_token: 是否生成一次性会话令牌（reflect 等内部会话可关闭）
        clear_dynamic_tools: 退出时是否清理 PFC 动态工具（tag 激活 + 动态发现）
    """
    from agent.mind.tool_activation import bind_scope, reset_scope, tool_activation
    from agent.security.session_token import bind_token, generate_token, reset_token

    scope_token = bind_scope(scope)
    token_ctx = bind_token(generate_token()) if with_token else None
    try:
        yield scope
    finally:
        # 会话收尾：消耗一轮激活周期 → 复位绑定 → 清理动态工具
        tool_activation.consume_round(scope)
        reset_scope(scope_token)
        if token_ctx is not None:
            reset_token(token_ctx)
        if clear_dynamic_tools:
            mind.pfc.clear_dynamic_tools()
