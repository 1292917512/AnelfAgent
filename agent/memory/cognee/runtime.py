"""Cognee 记忆组件的运行时引用。"""

from __future__ import annotations

from typing import Optional

from .client import CogneeClient
from .coordinator import CogneeCoordinator

_client: Optional[CogneeClient] = None
_coordinator: Optional[CogneeCoordinator] = None


def set_cognee_runtime(
    client: Optional[CogneeClient],
    coordinator: Optional[CogneeCoordinator],
) -> None:
    global _client, _coordinator
    _client = client
    _coordinator = coordinator


def get_cognee_client() -> Optional[CogneeClient]:
    return _client


def get_cognee_coordinator() -> Optional[CogneeCoordinator]:
    return _coordinator
