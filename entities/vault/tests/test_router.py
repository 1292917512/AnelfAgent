"""router 层冒烟测试：FastAPI 端点全链路（setup → unlock → CRUD → reveal → lock）。"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from entities.vault.router import build_router
from entities.vault.service import VaultService


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "entities.vault.service.get_config_int",
        lambda key, default=0: {
            "vault_kdf_time_cost": 1, "vault_kdf_memory_cost": 1024,
            "vault_kdf_parallelism": 1,
        }.get(key, default))
    import entities.vault.router as router_mod
    import entities.vault.service as svc_mod

    svc = VaultService(str(tmp_path / "vault.sqlite3"))
    monkeypatch.setattr(svc_mod, "_service", svc)
    monkeypatch.setattr(router_mod, "get_vault_service", lambda: svc)
    app = FastAPI()
    app.include_router(build_router(), prefix="/api/entity/vault")
    with TestClient(app) as test_client:
        yield test_client


class TestVaultApi:
    def test_full_lifecycle(self, client):
        # 初始状态：未初始化
        resp = client.get("/api/entity/vault/status")
        assert resp.status_code == 200
        assert resp.json() == {"initialized": False, "unlock_mode": "",
                               "unlocked": False, "entry_count": 0,
                               "auto_lock_remaining": 0}

        # 未初始化时写入自动建库（机器密钥模式，AI/用户零摩擦）
        resp = client.post("/api/entity/vault/entries", json={"title": "A", "password": "p"})
        assert resp.status_code == 201
        assert client.get("/api/entity/vault/status").json()["unlock_mode"] == "machine"
        entry_id = resp.json()["id"]
        client.delete(f"/api/entity/vault/entries/{entry_id}")

        # 主密码模式设置
        resp = client.post("/api/entity/vault/setup",
                           json={"mode": "master", "password": "master-pw-123"})
        assert resp.status_code == 409  # 已初始化（机器模式）不可重复 setup

        # 机器 → 主密码模式转换
        resp = client.post("/api/entity/vault/master/enable",
                           json={"password": "master-pw-123"})
        assert resp.status_code == 200
        assert resp.json()["unlock_mode"] == "master" and resp.json()["unlocked"]

        # 新增条目
        resp = client.post("/api/entity/vault/entries", json={
            "title": "GitHub", "username": "octo", "url": "https://github.com",
            "password": "secret-123", "tags": ["dev"]})
        assert resp.status_code == 201
        entry = resp.json()
        assert entry["has_password"] and "password" not in entry

        # 检索（不含明文）
        resp = client.get("/api/entity/vault/entries", params={"query": "github"})
        assert resp.status_code == 200
        assert resp.json()["entries"][0]["id"] == entry["id"]

        # 明文 reveal
        resp = client.post(f"/api/entity/vault/entries/{entry['id']}/reveal",
                           json={"field": "password"})
        assert resp.status_code == 200 and resp.json()["value"] == "secret-123"

        # 更新（None 保持不变）
        resp = client.put(f"/api/entity/vault/entries/{entry['id']}",
                          json={"title": "GitHub 工作号"})
        assert resp.status_code == 200
        resp = client.post(f"/api/entity/vault/entries/{entry['id']}/reveal",
                           json={"field": "password"})
        assert resp.json()["value"] == "secret-123"

        # 锁定后 reveal 被拒；错误密码 401；重新解锁恢复
        client.post("/api/entity/vault/lock")
        resp = client.post(f"/api/entity/vault/entries/{entry['id']}/reveal",
                           json={"field": "password"})
        assert resp.status_code == 423
        resp = client.post("/api/entity/vault/unlock", json={"password": "wrong-password"})
        assert resp.status_code == 401
        resp = client.post("/api/entity/vault/unlock", json={"password": "master-pw-123"})
        assert resp.status_code == 200

        # 生成器与删除
        resp = client.post("/api/entity/vault/generate", json={"length": 24})
        assert resp.status_code == 200 and len(resp.json()["password"]) == 24
        resp = client.delete(f"/api/entity/vault/entries/{entry['id']}")
        assert resp.status_code == 200
        resp = client.get(f"/api/entity/vault/entries/{entry['id']}")
        assert resp.status_code == 404

        # 主密码 → 机器模式（恢复自动解锁）
        resp = client.post("/api/entity/vault/master/disable")
        assert resp.status_code == 200 and resp.json()["unlock_mode"] == "machine"
        client.post("/api/entity/vault/lock")
        resp = client.post("/api/entity/vault/unlock", json={"password": ""})
        assert resp.status_code == 200 and resp.json()["unlocked"]

    def test_machine_setup_endpoint(self, client):
        resp = client.post("/api/entity/vault/setup", json={"mode": "machine"})
        assert resp.status_code == 201
        body = resp.json()
        assert body["unlock_mode"] == "machine" and body["unlocked"]
        # 无密码解锁端点直接可用
        resp = client.post("/api/entity/vault/unlock", json={"password": ""})
        assert resp.status_code == 200

    def test_import_export(self, client):
        client.post("/api/entity/vault/setup",
                    json={"mode": "master", "password": "master-pw-123"})
        resp = client.post("/api/entity/vault/import", json={
            "format": "chrome_csv",
            "content": "name,url,username,password,note\nG,https://g.com,u,p,\n"})
        assert resp.status_code == 200 and resp.json()["added"] == 1

        resp = client.post("/api/entity/vault/export",
                           json={"format": "encrypted", "master_password": "backup-pw-1"})
        assert resp.status_code == 200
        assert "anelf-vault-enc-v1" in resp.json()["content"]

        # 明文导出不含密文形态、包含真实密码
        resp = client.post("/api/entity/vault/export", json={"format": "csv"})
        assert resp.status_code == 200 and ",u,p," in resp.json()["content"]
