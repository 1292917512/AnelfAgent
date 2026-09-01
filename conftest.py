"""仓库级测试共享层：配置隔离 + embedding 注册表隔离 + 目录分层自动 marker + 线程泄漏检测。

作用于全部测试面（分层套件 tests/ 与模块内套件 <模块>/tests/ 均经此收口）：

- ConfigManager 隔离：测试态指向临时配置文件并清空内存态，
  防止任何测试回写覆盖真实 config/app_config.json。
- Embedding 注册表隔离：实体模块导入期注册的全局 backlog 不得挂载到
  测试内 worker（其 handler 会打开真实数据库，aiosqlite 线程泄漏挂住退出）。
- 自动 marker：tests/unit/ 与模块内 tests/（entities/<name>/tests、
  channels/<id>/tests）下的用例自动标记 unit，tests/integration/ 下自动标记
  integration，无需逐文件手写；可用 -m "not integration" 分层运行。
- 线程泄漏检测：会话结束时报告存活的非守护线程（如未关闭的 aiosqlite
  连接），这类泄漏会阻塞 pytest 进程退出、挂起 CI。
"""

from __future__ import annotations

import sys
import threading
import traceback
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).parent
_UNIT_DIR = _REPO_ROOT / "tests" / "unit"
_INTEGRATION_DIR = _REPO_ROOT / "tests" / "integration"
_MODULE_PARENTS = ("entities", "channels")


@pytest.fixture(autouse=True)
def _isolate_config_manager(tmp_path, monkeypatch: pytest.MonkeyPatch):
    """隔离 ConfigManager：测试态指向临时配置文件并清空内存态。"""
    from core.config import ConfigManager

    monkeypatch.setattr(
        ConfigManager, "_config_file", str(tmp_path / "app_config.json")
    )
    ConfigManager.clear()
    yield
    ConfigManager.clear()


@pytest.fixture(autouse=True)
def _wire_function_ports():
    """施绑纯函数型跨层端口（测试即组合根，对齐 wiring.wire_runtime 的施绑）。

    这些端口承载无状态函数实现（工作区路径解析 / 结果落盘 / 文件状态缓存 /
    图片索引投递），生产由 agent.runtime.wiring 施绑；测试环境无 bootstrap，
    在此统一施绑真实实现，保持被测路径与生产一致。
    """
    from agent.approval.policy import WorkspacePathFns, workspace_paths_port
    from agent.mind.tools.media_pipeline import image_index_submit_port
    from agent.mind.tools.result_pipeline import shell_persist_port
    from agent.mind.tools.round_helpers import file_state_cache_port
    from entities.filesystem.file_state import get_cache as get_file_state_cache
    from entities.filesystem.paths import get_workspace_root, resolve_workspace_path
    from entities.filesystem.shell_state import persist_output
    from entities.sticker.worker import submit_image

    workspace_paths_port.set(WorkspacePathFns(get_workspace_root, resolve_workspace_path))
    file_state_cache_port.set(get_file_state_cache)
    shell_persist_port.set(persist_output)
    image_index_submit_port.set(submit_image)
    yield
    workspace_paths_port.unbind()
    file_state_cache_port.unbind()
    shell_persist_port.unbind()
    image_index_submit_port.unbind()


@pytest.fixture(autouse=True)
def _isolate_embedding_registry():
    """隔离 EmbeddingWorker 端口与挂起注册表（embedding_worker_port / _pending_backlogs）。

    实体模块（如 entities.voiceprint.worker）在导入期向全局挂起表注册
    backlog；测试内施绑端口会将其挂载到测试 worker，handler 随即打开
    全局单例存储的真实数据库——aiosqlite 连接线程无人关闭，挂住 pytest
    进程退出。逐用例快照/清空/恢复，阻断跨层污染。
    """
    from agent.memory.embedding import worker as embedding_worker

    port = embedding_worker.embedding_worker_port
    was_bound = port.bound
    saved_worker = port.get() if was_bound else None
    saved_pending = dict(embedding_worker._pending_backlogs)
    port.unbind()
    embedding_worker._pending_backlogs.clear()
    yield
    port.unbind()
    if was_bound:
        port.set(saved_worker)
    embedding_worker._pending_backlogs.clear()
    embedding_worker._pending_backlogs.update(saved_pending)


def _is_module_test(path: Path) -> bool:
    """模块内测试判定：entities/<name>/tests/ 或 channels/<id>/tests/ 下的用例。"""
    try:
        rel = path.relative_to(_REPO_ROOT)
    except ValueError:
        return False
    return len(rel.parts) >= 3 and rel.parts[0] in _MODULE_PARENTS and rel.parts[2] == "tests"


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """按所在目录自动打上 unit / integration marker。"""
    for item in items:
        path = Path(str(item.fspath))
        try:
            if path.is_relative_to(_UNIT_DIR) or _is_module_test(path):
                item.add_marker(pytest.mark.unit)
            elif path.is_relative_to(_INTEGRATION_DIR):
                item.add_marker(pytest.mark.integration)
        except ValueError:
            continue


_thread_origins: dict[int, str] = {}
_thread_create_stacks: dict[int, str] = {}

_orig_thread_start = threading.Thread.start


def _tracked_thread_start(self: threading.Thread) -> None:
    """记录线程创建栈（会话结束仍有存活线程时用于归因）。"""
    try:
        stack = traceback.extract_stack(limit=12)[:-1]
        _thread_create_stacks[id(self)] = "".join(traceback.format_list(stack[-8:]))
    except Exception:
        pass
    _orig_thread_start(self)


threading.Thread.start = _tracked_thread_start  # type: ignore[method-assign]


@pytest.hookimpl(hookwrapper=True, tryfirst=True)
def pytest_runtest_call(item: pytest.Item):
    """记录每个用例执行期间新创建的线程，便于泄漏归因。"""
    before = set(threading.enumerate())
    outcome = yield
    for t in set(threading.enumerate()) - before:
        _thread_origins.setdefault(t.ident or 0, item.nodeid)
    return outcome


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    """报告退出时仍存活的非守护线程（会阻塞进程退出的泄漏）。"""
    # 共享线程池（async_helper 等）是刻意的长生命周期设计，Lifecycle 已注册
    # 关闭钩子；会话收尾主动关闭并从泄漏报告中排除，避免误报与归因错乱。
    try:
        from core.async_helper import shutdown_shared_executor

        shutdown_shared_executor()
    except Exception:
        pass
    leaked = [
        t for t in threading.enumerate()
        if t.is_alive() and not t.daemon and t is not threading.main_thread()
        and not t.name.startswith("async_helper")
    ]
    if leaked:
        print(f"\n[thread-leak] {len(leaked)} 个非守护线程仍存活，将阻塞 pytest 退出：")
        frames = sys._current_frames()
        for t in leaked:
            origin = _thread_origins.get(t.ident or 0, "未知用例")
            print(f"  - {t.name} (ident={t.ident}) 创建于: {origin}")
            create_stack = _thread_create_stacks.get(id(t))
            if create_stack:
                print(f"    创建栈:\n{create_stack}")
            frame = frames.get(t.ident or 0)
            if frame is not None:
                stack = "".join(traceback.format_stack(frame, limit=5))
                print(f"    当前栈顶:\n{stack}")
