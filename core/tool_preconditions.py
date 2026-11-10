"""工具调用前置条件端口；由组合根注入领域校验，所有注册表执行通道共用。"""

from __future__ import annotations

from collections.abc import Callable

from core.latebind import LateBinding

tool_precondition_port: LateBinding[Callable[[str], str | None]] = LateBinding("tools.precondition")
