"""Dify 连接编排单元测试（Mock HTTP 客户端，无真实网络/配置污染）。"""
from __future__ import annotations

from typing import Any, Dict, Optional

import pytest

from core.config import ConfigManager
from entities.dify import service
from entities.dify.client import DifyAuthError
from entities.dify.config import _reset_store_for_tests
from entities.dify.service import DifyStateError


class FakeConsoleClient:
    """模拟 DifyConsoleClient 的最小替身（按构造参数决定行为）。"""

    # 类级开关：由测试用例设置
    probe_result: Optional[Dict[str, Any]] = {"step": "finished"}
    version: str = "1.17.0"
    login_error: Optional[Exception] = None
    setup_calls: list = []

    def __init__(self, base_url: str, *, timeout: float = 30.0) -> None:
        self.base_url = base_url
        self._credentials: Dict[str, str] = {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None

    def set_credentials(self, email: str, password: str) -> None:
        self._credentials = {"email": email, "password": password}

    async def probe(self):
        return type(self).probe_result

    async def fetch_version(self) -> str:
        return type(self).version

    async def setup_status(self):
        result = type(self).probe_result
        if result is None:
            raise DifyStateError("unreachable")
        return result

    async def setup_admin(self, email: str, name: str, password: str, language: str = "zh-Hans"):
        type(self).setup_calls.append({"email": email, "password": password})

    async def login(self, email: str, password: str):
        if type(self).login_error:
            raise type(self).login_error
        self._credentials = {"email": email, "password": password}


@pytest.fixture(autouse=True)
def _isolate(monkeypatch: pytest.MonkeyPatch, tmp_path):
    """隔离配置与密钥库，并把 HTTP 客户端换成替身。"""
    monkeypatch.setattr(service, "DifyConsoleClient", FakeConsoleClient)
    _reset_store_for_tests(str(tmp_path / "secrets.json"))
    ConfigManager.set("dify_base_url", "http://dify.test")
    ConfigManager.set("dify_enabled", True)
    ConfigManager.set("dify_auto_setup", True)
    ConfigManager.set("dify_admin_email", "admin@dify.local")
    FakeConsoleClient.probe_result = {"step": "finished"}
    FakeConsoleClient.version = "1.17.0"
    FakeConsoleClient.login_error = None
    FakeConsoleClient.setup_calls = []
    yield


@pytest.mark.asyncio
async def test_status_unconfigured():
    """未配置地址时：configured=False，不做网络探测。"""
    ConfigManager.set("dify_base_url", "")
    status = await service.get_status()
    assert status["configured"] is False
    assert status["reachable"] is False
    assert status["admin_configured"] is False


@pytest.mark.asyncio
async def test_status_unreachable():
    """地址已配置但不可达：reachable=False，不抛异常。"""
    FakeConsoleClient.probe_result = None
    status = await service.get_status()
    assert status["configured"] is True
    assert status["reachable"] is False


@pytest.mark.asyncio
async def test_connect_unreachable_raises():
    FakeConsoleClient.probe_result = None
    with pytest.raises(DifyStateError, match="无法连接"):
        await service.connect()


@pytest.mark.asyncio
async def test_connect_requires_base_url():
    ConfigManager.set("dify_base_url", "")
    with pytest.raises(DifyStateError, match="地址未配置"):
        await service.connect()


@pytest.mark.asyncio
async def test_connect_auto_setup_creates_admin():
    """未初始化实例：自动创建管理员并保存凭据。"""
    FakeConsoleClient.probe_result = {"step": "not_started"}
    result = await service.connect()
    assert result["ok"] is True
    assert result["auto_created"] is True
    assert result["email"] == "admin@dify.local"
    assert len(FakeConsoleClient.setup_calls) == 1
    from entities.dify.config import get_dify_store
    admin = get_dify_store().get_admin()
    assert admin["email"] == "admin@dify.local"
    assert admin["password"]  # 随机密码已保存
    assert admin["created_by"] == "auto"


@pytest.mark.asyncio
async def test_connect_auto_setup_disabled():
    """auto_setup 关闭时：不自动创建，返回指引。"""
    ConfigManager.set("dify_auto_setup", False)
    FakeConsoleClient.probe_result = {"step": "not_started"}
    result = await service.connect()
    assert result["ok"] is True
    assert "auto_created" not in result
    assert FakeConsoleClient.setup_calls == []


@pytest.mark.asyncio
async def test_connect_existing_credentials_verified():
    """已初始化实例 + 已有凭据：登录验证通过。"""
    from entities.dify.config import get_dify_store
    get_dify_store().set_admin("a@b.c", "pw", created_by="manual")
    result = await service.connect()
    assert result["ok"] is True
    assert result["message"].startswith("连接成功")
    assert result["version"] == "1.17.0"


@pytest.mark.asyncio
async def test_connect_stale_credentials_rejected():
    """已初始化实例 + 凭据失效：给出更新指引。"""
    from entities.dify.config import get_dify_store
    get_dify_store().set_admin("a@b.c", "wrong", created_by="manual")
    FakeConsoleClient.login_error = DifyAuthError("bad credentials", status=401)
    with pytest.raises(DifyStateError, match="凭据登录失败"):
        await service.connect()


@pytest.mark.asyncio
async def test_setup_admin_manual_saves_after_verify():
    result = await service.setup_admin_manual("admin@x.com", "pw-123")
    assert result["ok"] is True
    from entities.dify.config import get_dify_store
    admin = get_dify_store().get_admin()
    assert admin["email"] == "admin@x.com"
    assert admin["created_by"] == "manual"


@pytest.mark.asyncio
async def test_setup_admin_manual_rejects_bad_login():
    FakeConsoleClient.login_error = DifyAuthError("bad", status=401)
    with pytest.raises(DifyAuthError):
        await service.setup_admin_manual("admin@x.com", "wrong")
    from entities.dify.config import get_dify_store
    assert get_dify_store().get_admin() == {}  # 验证失败不保存


@pytest.mark.asyncio
async def test_operations_require_connection():
    """业务操作在地址未配置时给出明确指引。"""
    ConfigManager.set("dify_base_url", "")
    with pytest.raises(DifyStateError, match="地址未配置"):
        await service.list_apps()
