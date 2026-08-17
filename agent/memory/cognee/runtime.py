"""Cognee 记忆组件的运行时端口（可选后端）。

client/coordinator 经 LateBinding 端口持有，bootstrap 经
``agent.runtime.wiring`` 统一施绑（初始化失败时以 None 施绑，
None 是合法绑定值）；get_* 访问器在施绑前返回 None，与旧模块级
全局的语义一致。
"""
from __future__ import annotations

from typing import Optional

from core.latebind import LateBinding

from .client import CogneeClient
from .coordinator import CogneeCoordinator

#: Cognee 客户端端口（未启用/初始化失败时施绑 None）
cognee_client_port: LateBinding[Optional[CogneeClient]] = LateBinding(
    "memory.cognee.client",
)

#: Cognee 协调器端口（未启用/初始化失败时施绑 None）
cognee_coordinator_port: LateBinding[Optional[CogneeCoordinator]] = LateBinding(
    "memory.cognee.coordinator",
)


def get_cognee_client() -> Optional[CogneeClient]:
    """Cognee 客户端（施绑前返回 None）。"""
    return cognee_client_port.get() if cognee_client_port.bound else None


def get_cognee_coordinator() -> Optional[CogneeCoordinator]:
    """Cognee 协调器（施绑前返回 None）。"""
    return cognee_coordinator_port.get() if cognee_coordinator_port.bound else None
