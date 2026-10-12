"""模块注册的撤销日志；仅回滚当前模块的声明，不回滚其公共依赖。"""

from __future__ import annotations

import importlib
import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from copy import copy
from dataclasses import dataclass, field
from types import FrameType, ModuleType
from typing import MutableMapping, TypeVar

from core.log import log


def belongs_to(module: str, owner: str) -> bool:
    """判断模块是否属于指定包，避免相同名称前缀误匹配。"""
    return module == owner or module.startswith(owner + ".")


def caller_module() -> str:
    """获取注册声明所在模块，跳过核心设施和显式标记的 SDK 桥。"""
    frame: FrameType | None = sys._getframe(1)
    try:
        while frame is not None:
            name = str(frame.f_globals.get("__name__", ""))
            if not name.startswith("core.") and not frame.f_globals.get("__registration_bridge__", False):
                return name
            frame = frame.f_back
        return ""
    finally:
        del frame


@dataclass
class _Transaction:
    owner: str
    undos: list[Callable[[], None]] = field(default_factory=list)
    active: bool = True


_transaction: ContextVar[_Transaction | None] = ContextVar("module_registration", default=None)
K = TypeVar("K")
V = TypeVar("V")


def record_undo(undo: Callable[[], None], *, owner: str = "") -> None:
    """为正在加载的模块登记内存注册撤销动作。"""
    transaction = _transaction.get()
    if transaction is not None and transaction.active and belongs_to(owner or caller_module(), transaction.owner):
        transaction.undos.append(undo)


def record_mapping(mapping: MutableMapping[K, V], key: K, *, owner: str = "") -> None:
    """记录一个注册表键的旧值；容器值做浅拷贝以隔离后续原地修改。"""
    snapshot: dict[K, V] = {}
    if key in mapping:
        previous = mapping[key]
        snapshot[key] = copy(previous) if isinstance(previous, (dict, list, set)) else previous

    def undo() -> None:
        if snapshot:
            mapping.update(snapshot)
        else:
            mapping.pop(key, None)
    record_undo(undo, owner=owner)


@contextmanager
def registration_transaction(owner: str) -> Iterator[None]:
    """注册成功后提交；失败按逆序撤销本模块的注册副作用。"""
    transaction = _Transaction(owner)
    token = _transaction.set(transaction)
    try:
        yield
    except BaseException:
        transaction.active = False
        for undo in reversed(transaction.undos):
            try:
                undo()
            except Exception as exc:
                log(f"模块注册回滚失败: {owner}: {exc}", "ERROR")
        raise
    finally:
        transaction.active = False
        _transaction.reset(token)


def import_registered_module(name: str, *, owner: str) -> ModuleType:
    """导入模块；失败撤销声明并移除本次加载的私有子模块，允许修复后重试。"""
    previous_modules = set(sys.modules)
    try:
        with registration_transaction(owner):
            return importlib.import_module(name)
    except BaseException:
        for key in set(sys.modules) - previous_modules:
            if belongs_to(key, owner):
                sys.modules.pop(key, None)
        raise
