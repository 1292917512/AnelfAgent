"""tools 层测试：AI 总开关门控 / 解锁态门控 / 风险标记 / 错误归因。"""

import json

import pytest

from core.entity import EntityRegistry
from entities.vault import tools as vault_tools


@pytest.fixture
async def service(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "entities.vault.service.get_config_int",
        lambda key, default=0: {
            "vault_kdf_time_cost": 1, "vault_kdf_memory_cost": 1024,
            "vault_kdf_parallelism": 1,
        }.get(key, default))
    import entities.vault.service as svc_mod

    svc = svc_mod.VaultService(str(tmp_path / "vault.sqlite3"))
    await svc.initialize()
    monkeypatch.setattr(svc_mod, "_service", svc)
    monkeypatch.setattr(vault_tools, "get_vault_service", lambda: svc)
    yield svc
    await svc.close()


class TestAiGate:
    async def test_disabled_returns_state_error(self, monkeypatch):
        monkeypatch.setattr(vault_tools, "get_config_bool",
                            lambda key, default=False: False)
        result = json.loads(await vault_tools.vault_search("x"))
        assert result["error"] and result["cause"] == "state"

    async def test_enabled_passes(self, service):
        result = json.loads(await vault_tools.vault_search("x"))
        assert "entries" in result


class TestLockedGate:
    async def test_reveal_locked(self, service):
        await service.setup("master-pw-123")
        service.lock()
        result = json.loads(await vault_tools.vault_reveal("any-id"))
        assert result["cause"] == "state"
        assert "解锁" in result["hint"]

    async def test_add_auto_provisions_machine_mode(self, service):
        """未初始化时首次写入自动建库（机器密钥模式），AI 零摩擦。"""
        result = json.loads(await vault_tools.vault_add(title="A", password="p-12345"))
        assert result["added"]["has_password"]
        assert await service.is_initialized()

    async def test_add_locked_master_mode(self, service):
        """主密码模式锁定后写敏感数据返回引导错误。"""
        await service.setup("master-pw-123")
        service.lock()
        result = json.loads(await vault_tools.vault_add(title="A", password="p"))
        assert result["cause"] == "state"
        assert "解锁" in result["hint"]

    async def test_vault_unlock_tool(self, service):
        """AI 可用主人在对话中提供的主密码解锁。"""
        await service.setup("master-pw-123")
        service.lock()
        bad = json.loads(await vault_tools.vault_unlock("wrong-password"))
        assert bad["cause"] in ("param", "state")
        good = json.loads(await vault_tools.vault_unlock("master-pw-123"))
        assert good["unlocked"]

    async def test_search_works_while_locked(self, service):
        """检索只读元数据，不需要解锁。"""
        await service.setup("master-pw-123")
        await service.add_entry(title="GitHub", username="u")
        service.lock()
        result = json.loads(await vault_tools.vault_search("github"))
        assert result["count"] == 1


class TestToolFlow:
    async def test_add_search_get_update_delete(self, service):
        await service.setup("master-pw-123")
        added = json.loads(await vault_tools.vault_add(
            title="GitHub", username="octo", password="pw-12345", tags="dev,code"))
        entry_id = added["added"]["id"]

        found = json.loads(await vault_tools.vault_search("git"))
        assert found["entries"][0]["id"] == entry_id

        detail = json.loads(await vault_tools.vault_get(entry_id))
        assert detail["password"] == "********"

        revealed = json.loads(await vault_tools.vault_reveal(entry_id))
        assert revealed["password"] == "pw-12345"
        assert "禁止写入记忆" in revealed["notice"]

        updated = json.loads(await vault_tools.vault_update(
            entry_id, title="GitHub 工作号"))
        assert updated["updated"]["title"] == "GitHub 工作号"

        deleted = json.loads(await vault_tools.vault_delete(entry_id))
        assert deleted["deleted"] == entry_id

    async def test_add_with_generate(self, service):
        await service.setup("master-pw-123")
        result = json.loads(await vault_tools.vault_add(title="A", generate=True))
        assert len(result["generated_password"]) >= 8

    async def test_not_found(self, service):
        result = json.loads(await vault_tools.vault_get("no-such-id"))
        assert result["cause"] == "not_found"

    async def test_generate_tool(self, service):
        result = json.loads(await vault_tools.vault_generate(length=24))
        assert len(result["password"]) == 24
        assert result["strength"]["level"] in ("strong", "excellent")


class TestRiskMeta:
    @pytest.mark.parametrize("name", ["vault_reveal", "vault_totp", "vault_delete"])
    def test_sensitive_tools_marked_critical(self, name):
        entity = EntityRegistry.get(name)
        assert entity is not None
        assert entity.meta.get("risk") == "CRITICAL"
