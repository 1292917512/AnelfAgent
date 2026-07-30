"""tests 全局共享层：配置隔离 + 目录分层自动 marker + 线程泄漏检测。

- ConfigManager 隔离：测试态指向临时配置文件并清空内存态，
  防止任何测试回写覆盖真实 config/app_config.json。
- 自动 marker：tests/unit/ 下用例自动标记 unit，tests/integration/ 下自动标记
  integration，无需逐文件手写；可用 -m "not integration" 分层运行。
- 线程泄漏检测：会话结束时报告存活的非守护线程（如未关闭的 aiosqlite
  连接），这类泄漏会阻塞 pytest 进程退出、挂起 CI。
"""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

_UNIT_DIR = Path(__file__).parent / "unit"
_INTEGRATION_DIR = Path(__file__).parent / "integration"


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


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """按所在目录自动打上 unit / integration marker。"""
    for item in items:
        path = Path(str(item.fspath))
        try:
            if path.is_relative_to(_UNIT_DIR):
                item.add_marker(pytest.mark.unit)
            elif path.is_relative_to(_INTEGRATION_DIR):
                item.add_marker(pytest.mark.integration)
        except ValueError:
            continue


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    """报告退出时仍存活的非守护线程（会阻塞进程退出的泄漏）。"""
    leaked = [
        t for t in threading.enumerate()
        if t.is_alive() and not t.daemon and t is not threading.main_thread()
    ]
    if leaked:
        names = ", ".join(t.name for t in leaked)
        print(
            f"\n[thread-leak] {len(leaked)} 个非守护线程仍存活（{names}），"
            "将阻塞 pytest 退出——请检查未关闭的连接/资源（如 MemoryStore.close()）。"
        )
