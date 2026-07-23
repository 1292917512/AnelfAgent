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
