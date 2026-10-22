"""存储卷声明装配；启动恢复与管理界面共享同一模块清单，不打开数据库。"""

from __future__ import annotations

import importlib

from core.log import log

VOLUME_MODULES = (
    "agent.storage.sqlite_backend",
    "agent.memory.memory_store",
    "agent.memory.notes",
    "agent.memory.cognee.config",
    "agent.skills.skill_index",
    "entities.sticker.store",
    "agent.audio.store",
    "entities.share.store",
)


def ensure_volume_modules() -> None:
    """导入声明模块完成卷登记，供打开存储前的恢复与管理端调用。"""
    for name in VOLUME_MODULES:
        try:
            importlib.import_module(name)
        except Exception as exc:
            log(f"存储卷模块加载失败 {name}: {exc}", "DEBUG", tag="数据库")
