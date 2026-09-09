"""Dify 密钥库存取与脱敏单元测试。"""
from __future__ import annotations

import json
import os

import pytest

from entities.dify.config import DifySecretsStore, generate_hex_token, generate_password


@pytest.fixture
def store(tmp_path):
    return DifySecretsStore(str(tmp_path / "secrets.json"))


def test_admin_roundtrip(store):
    """管理员凭据写入后可读回，文件落盘且权限 0600。"""
    store.set_admin("admin@dify.local", "s3cret", created_by="auto")
    admin = store.get_admin()
    assert admin["email"] == "admin@dify.local"
    assert admin["password"] == "s3cret"
    assert admin["created_by"] == "auto"
    assert os.path.exists(store._path)
    if os.name != "nt":
        assert (os.stat(store._path).st_mode & 0o777) == 0o600


def test_admin_masking():
    """脱敏出站替换密码为占位符。"""
    masked = DifySecretsStore.mask_admin({"email": "a@b.c", "password": "real"})
    assert masked["password"] == "********"
    assert masked["email"] == "a@b.c"
    assert DifySecretsStore.is_masked(masked["password"])
    assert not DifySecretsStore.is_masked("real")


def test_app_entries(store):
    """应用条目合并写入：未传字段保留，None 删除字段。"""
    store.update_app("app-1", name="客服", api_key="app-xxx")
    store.update_app("app-1", mcp_server_code="code123")
    entry = store.get_app("app-1")
    assert entry["name"] == "客服"
    assert entry["api_key"] == "app-xxx"
    assert entry["mcp_server_code"] == "code123"
    assert store.get_api_key("app-1") == "app-xxx"
    assert store.find_app_by_name("客服") == "app-1"

    store.update_app("app-1", mcp_server_code=None)
    assert "mcp_server_code" not in store.get_app("app-1")

    store.remove_app("app-1")
    assert store.get_app("app-1") == {}
    assert store.find_app_by_name("客服") is None


def test_app_masking():
    masked = DifySecretsStore.mask_app({"name": "x", "api_key": "app-secret"})
    assert masked["api_key"] == "********"


def test_deploy_state(store):
    store.set_deploy(version="1.17.0", deployed_at=123.0)
    deploy = store.get_deploy()
    assert deploy["version"] == "1.17.0"
    store.set_deploy(version="1.18.0")
    assert store.get_deploy()["deployed_at"] == 123.0  # 未传字段保留


def test_corrupt_file_falls_back_to_empty(tmp_path):
    """损坏的 secrets.json 按空配置处理，不抛异常。"""
    path = tmp_path / "secrets.json"
    path.write_text("{not json", encoding="utf-8")
    store = DifySecretsStore(str(path))
    assert store.get_admin() == {}
    store.set_admin("a@b.c", "p")
    assert store.get_admin()["password"] == "p"


def test_persistence_across_instances(tmp_path):
    """两个实例读同一文件：持久化内容互通。"""
    path = str(tmp_path / "secrets.json")
    DifySecretsStore(path).set_admin("a@b.c", "p")
    assert DifySecretsStore(path).get_admin()["email"] == "a@b.c"


def test_generators():
    assert len(generate_password(20)) == 20
    assert len(generate_hex_token(24)) == 48
    assert generate_password() != generate_password()
