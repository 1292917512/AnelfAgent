"""思维工具组共享的运行时端口。

工具在导入时声明，Mind 在运行时装配后经 LateBinding 端口分发。
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from core.latebind import LateBinding

if TYPE_CHECKING:
    from agent.mind.mind import Mind

#: 各思维工具共享的 Mind 端口，由 agent.runtime.wiring 施绑。
mind_port: LateBinding["Mind"] = LateBinding("mind.tools")
