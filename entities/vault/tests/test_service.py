"""service 层测试：主密码生命周期 + 条目 CRUD + 检索 + 导入导出。"""

import pytest

from entities.vault import service as svc_mod
from entities.vault.service import (
    VaultAlreadyInitializedError,
    VaultAuthError,
    VaultService,
)
from entities.vault.session import VaultLockedError


@pytest.fixture
async def service(tmp_path, monkeypatch):
    # 测试用小参数 KDF
    monkeypatch.setattr(svc_mod.crypto, "DEFAULT_TIME_COST", 1)
    monkeypatch.setattr(svc_mod.crypto, "DEFAULT_MEMORY_COST", 1024)
    monkeypatch.setattr(svc_mod.crypto, "DEFAULT_PARALLELISM", 1)
    monkeypatch.setattr(
        "entities.vault.service.get_config_int",
        lambda key, default=0: {
            "vault_kdf_time_cost": 1, "vault_kdf_memory_cost": 1024,
            "vault_kdf_parallelism": 1,
        }.get(key, default))
    service = VaultService(str(tmp_path / "vault.sqlite3"))
    await service.initialize()
    yield service
    await service.close()


class TestMachineMode:
    """机器密钥模式：AI 优先的默认路径（自动建库 + 自动解锁）。"""

    async def test_auto_provision_on_first_write(self, service):
        """未初始化时首次写入自动建库（机器模式），AI 零摩擦。"""
        assert not await service.is_initialized()
        entry = await service.add_entry(title="GitHub", password="tok-1")
        assert await service.is_initialized()
        status = await service.status()
        assert status["unlock_mode"] == "machine" and status["unlocked"]
        assert await service.reveal(entry["id"]) == "tok-1"

    async def test_restart_auto_unlock(self, service):
        """重启（新实例同库）后自动解锁，无需任何人工操作。"""
        await service.add_entry(title="A", password="p-1")
        db_path = service.store.db_path
        await service.close()
        restarted = VaultService(db_path)
        await restarted.initialize()
        try:
            assert restarted.session.is_unlocked
            assert (await restarted.search("A"))[0]["title"] == "A"
        finally:
            await restarted.close()

    async def test_master_mode_restart_stays_locked(self, service):
        """主密码模式重启后保持锁定。"""
        await service.setup("master-pw-123")
        await service.add_entry(title="A", password="p-1")
        db_path = service.store.db_path
        await service.close()
        restarted = VaultService(db_path)
        await restarted.initialize()
        try:
            assert not restarted.session.is_unlocked
            await restarted.unlock("master-pw-123")
            assert restarted.session.is_unlocked
        finally:
            await restarted.close()

    async def test_enable_disable_master_roundtrip(self, service):
        """机器 → 主密码 → 机器双向转换，条目不重加密仍可读。"""
        entry = await service.add_entry(title="A", password="p-1")
        await service.enable_master("master-pw-123")
        assert (await service.status())["unlock_mode"] == "master"
        service.lock()
        with pytest.raises(VaultLockedError):
            await service.reveal(entry["id"])
        await service.unlock("master-pw-123")
        assert await service.reveal(entry["id"]) == "p-1"
        await service.disable_master()
        assert (await service.status())["unlock_mode"] == "machine"
        service.lock()
        assert await service.reveal(entry["id"]) == "p-1"  # 透明重解锁

    async def test_machine_key_file_permissions(self, service):
        import os
        import stat

        await service.setup_machine()
        key_path = f"{service.store.db_path}.key"
        mode = stat.S_IMODE(os.stat(key_path).st_mode)
        assert mode == 0o600

    async def test_missing_key_file_detected(self, service):
        """密钥文件与库不匹配时给出明确错误而非静默失败。"""
        import os

        await service.setup_machine()
        service.lock()
        os.remove(f"{service.store.db_path}.key")  # 新密钥将无法解包
        with pytest.raises(Exception, match="不匹配"):
            await service.reveal("any")


class TestMasterPassword:
    async def test_setup_and_status(self, service):
        assert not await service.is_initialized()
        await service.setup("master-pw-123")
        assert await service.is_initialized()
        status = await service.status()
        assert status["initialized"] and status["unlocked"]

    async def test_setup_twice_rejected(self, service):
        await service.setup("master-pw-123")
        with pytest.raises(VaultAlreadyInitializedError):
            await service.setup("master-pw-456")

    async def test_short_master_rejected(self, service):
        with pytest.raises(Exception, match="至少 8 位"):
            await service.setup("short")

    async def test_unlock_wrong_password(self, service):
        await service.setup("master-pw-123")
        service.lock()
        with pytest.raises(VaultAuthError):
            await service.unlock("wrong-password")

    async def test_unlock_after_lock(self, service):
        await service.setup("master-pw-123")
        service.lock()
        assert not service.session.is_unlocked
        await service.unlock("master-pw-123")
        assert service.session.is_unlocked

    async def test_change_master_preserves_data(self, service):
        await service.setup("master-pw-123")
        entry = await service.add_entry(title="GitHub", password="tok-abc-123")
        await service.change_master("master-pw-123", "new-master-pw")
        service.lock()
        with pytest.raises(VaultAuthError):
            await service.unlock("master-pw-123")
        await service.unlock("new-master-pw")
        assert await service.reveal(entry["id"]) == "tok-abc-123"


class TestEntries:
    @pytest.fixture
    async def unlocked(self, service):
        await service.setup("master-pw-123")
        return service

    async def test_add_and_get(self, unlocked):
        entry = await unlocked.add_entry(
            title="GitHub", username="octocat", url="https://github.com",
            password="secret-123", tags=["dev", "code"], favorite=True)
        assert entry["has_password"] and entry["favorite"]
        fetched = await unlocked.get_entry(entry["id"])
        assert fetched["title"] == "GitHub"
        assert "password_enc" not in fetched  # 公开输出不含密文

    async def test_reveal(self, unlocked):
        entry = await unlocked.add_entry(title="A", password="secret-123")
        assert await unlocked.reveal(entry["id"]) == "secret-123"

    async def test_reveal_requires_unlock(self, unlocked):
        entry = await unlocked.add_entry(title="A", password="secret-123")
        unlocked.lock()
        with pytest.raises(VaultLockedError):
            await unlocked.reveal(entry["id"])

    async def test_update_semantics(self, unlocked):
        entry = await unlocked.add_entry(title="A", password="p1", notes="n1")
        # None = 保持不变
        await unlocked.update_entry(entry["id"], title="A2")
        assert await unlocked.reveal(entry["id"], "password") == "p1"
        # 空串 = 清除
        await unlocked.update_entry(entry["id"], notes="")
        fetched = await unlocked.get_entry(entry["id"])
        assert not fetched["has_notes"]
        # 非空 = 重写
        await unlocked.update_entry(entry["id"], password="p2")
        assert await unlocked.reveal(entry["id"]) == "p2"

    async def test_delete(self, unlocked):
        entry = await unlocked.add_entry(title="A")
        await unlocked.delete_entry(entry["id"])
        with pytest.raises(Exception, match="不存在"):
            await unlocked.get_entry(entry["id"])

    async def test_search_ranking(self, unlocked):
        await unlocked.add_entry(title="GitHub 工作号", username="work@corp.com")
        await unlocked.add_entry(title="Gmail", url="https://mail.google.com")
        results = await unlocked.search("github")
        assert results[0]["title"] == "GitHub 工作号"
        assert results[0]["score"] > 0
        # URL 归一化：站点名命中完整 URL
        results = await unlocked.search("mail.google.com")
        assert results[0]["title"] == "Gmail"

    async def test_search_filters(self, unlocked):
        await unlocked.add_entry(title="A", tags=["work"], favorite=True)
        await unlocked.add_entry(title="B", tags=["life"])
        assert len(await unlocked.search(tag="work")) == 1
        assert len(await unlocked.search(favorite_only=True)) == 1


class TestImportExport:
    @pytest.fixture
    async def unlocked(self, service):
        await service.setup("master-pw-123")
        return service

    async def test_encrypted_export_roundtrip(self, unlocked):
        await unlocked.add_entry(title="A", username="u", password="p-12345")
        blob = await unlocked.export("encrypted", master_password="backup-pw-1")
        other = VaultService(str(unlocked.store.db_path) + ".other")
        await other.initialize()
        try:
            await other.setup("other-master-pw")
            report = await other.import_text(blob, "encrypted",
                                             master_password="backup-pw-1")
            assert report["added"] == 1
            assert await other.reveal(
                (await other.search("A"))[0]["id"]) == "p-12345"
        finally:
            await other.close()

    async def test_import_dedup_skip(self, unlocked):
        from entities.vault.portable import EntryDraft

        await unlocked.add_entry(title="A", username="u", url="x.com")
        report = await unlocked.import_drafts(
            [EntryDraft(title="a", username="U", url="X.com")], strategy="skip")
        assert report["skipped"] == 1 and report["added"] == 0
