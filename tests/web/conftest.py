"""tests/web 共享夹具：隔离 API 鉴权。

web 测试直接构建真实应用，若读取到本机 config/webui.json 中已设置的密码，
所有 /api/* 请求会被认证中间件拦截为 401。此处测试态将密码视为空，
使中间件放行；不影响生产环境的鉴权行为。
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _bypass_api_password(monkeypatch: pytest.MonkeyPatch) -> None:
    """测试态关闭 /api/* 密码保护，避免本机配置密码导致全部 401。"""
    import web.server as server

    monkeypatch.setattr(server, "_load_auth_password", lambda: "")


@pytest.fixture(autouse=True)
def _isolate_config_manager(tmp_path, monkeypatch: pytest.MonkeyPatch):
    """隔离 ConfigManager：测试态指向临时配置文件并清空内存态。

    配置元数据 API 会调用 ConfigManager.save() 全量回写配置文件；
    未隔离时测试进程会把真实 config/app_config.json 覆盖为仅剩测试键。
    """
    from core.config import ConfigManager

    monkeypatch.setattr(
        ConfigManager, "_config_file", str(tmp_path / "app_config.json")
    )
    ConfigManager.clear()
    yield
    ConfigManager.clear()
