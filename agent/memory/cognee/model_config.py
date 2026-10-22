"""宿主与 SDK 复用的模型配置解析；不导入或打开 Cognee 数据库。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .config import CogneeConfig
from .embedding_identity import validate_embedding
from .llm_bridge import CogneeConfigError, resolve_chat_llm_config, resolve_embedding_llm_config


def resolve_models(config: CogneeConfig) -> tuple[dict[str, Any], dict[str, Any]]:
    from agent.llm import get_llm_manager

    manager = get_llm_manager()
    chat = resolve_chat_llm_config(config.chat, manager)
    embedding = resolve_embedding_llm_config(config.embedding, manager)
    if not embedding:
        raise CogneeConfigError("尚未配置 Embedding 模型，请在模型管理中添加")
    return chat, validate_embedding(Path(config.absolute_data_root), embedding)


def validate_models(config: CogneeConfig) -> None:
    if not config.enabled:
        return
    from agent.llm import get_llm_manager
    from agent.llm.llm_client import ModelType

    manager = get_llm_manager()
    if config.chat.source != "auto" or manager.get_all_by_type(ModelType.CHAT):
        resolve_chat_llm_config(config.chat, manager)
    payload = resolve_embedding_llm_config(config.embedding, manager)
    if payload:
        validate_embedding(Path(config.absolute_data_root), payload)
