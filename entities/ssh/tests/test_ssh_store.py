"""SshConfigStore 连接配置持久化测试。

覆盖：CRUD、字段校验、默认连接管理、重命名、${ENV_VAR} 引用保留、文件权限。
"""

from __future__ import annotations

import os

import pytest

from entities.ssh.store import SshConfigStore


@pytest.fixture
def store(tmp_path):
    return SshConfigStore(str(tmp_path / "connections.json"))


@pytest.fixture
def profile():
    return {
        "name": "web",
        "host": "192.168.1.10",
        "port": 22,
        "username": "root",
        "password": "secret",
        "description": "生产 Web",
    }


class TestSaveAndGet:
    async def test_save_and_get(self, store: SshConfigStore, profile: dict) -> None:
        entry = await store.save(profile)
        assert entry["name"] == "web"
        got = store.get("web")
        assert got is not None
        assert got["host"] == "192.168.1.10"
        assert got["username"] == "root"

    async def test_get_missing_returns_none(self, store: SshConfigStore) -> None:
        assert store.get("nonexistent") is None

    async def test_first_connection_becomes_default(self, store: SshConfigStore, profile: dict) -> None:
        await store.save(profile)
        assert store.get_default_name() == "web"

    async def test_update_preserves_created_at(self, store: SshConfigStore, profile: dict) -> None:
        first = await store.save(profile)
        updated = await store.save({**profile, "host": "10.0.0.1"})
        assert updated["host"] == "10.0.0.1"
        assert updated["created_at"] == first["created_at"]


class TestValidation:
    @pytest.mark.parametrize(
        "override",
        [
            {"name": ""},                       # 空名称
            {"name": "has space"},              # 非法字符
            {"host": ""},                       # 空主机
            {"username": ""},                   # 空用户名
            {"port": 0},                        # 端口过小
            {"port": 70000},                    # 端口过大
            {"password": "", "key_path": ""},   # 无任何凭据
        ],
    )
    async def test_invalid_profile_raises(self, store: SshConfigStore, profile: dict, override: dict) -> None:
        with pytest.raises(ValueError):
            await store.save({**profile, **override})

    async def test_key_only_auth_is_valid(self, store: SshConfigStore, profile: dict) -> None:
        entry = await store.save({**profile, "password": "", "key_path": "~/.ssh/id_rsa"})
        assert entry["key_path"] == "~/.ssh/id_rsa"


class TestDefault:
    async def test_set_default(self, store: SshConfigStore, profile: dict) -> None:
        await store.save(profile)
        await store.save({**profile, "name": "db", "host": "10.0.0.2"})
        await store.set_default("db")
        assert store.get_default_name() == "db"

    async def test_set_default_missing_raises(self, store: SshConfigStore, profile: dict) -> None:
        await store.save(profile)
        with pytest.raises(ValueError):
            await store.set_default("nonexistent")


class TestRename:
    async def test_rename(self, store: SshConfigStore, profile: dict) -> None:
        await store.save(profile)
        await store.save({**profile, "name": "web2"}, rename_from="web")
        assert store.get("web") is None
        assert store.get("web2") is not None

    async def test_rename_updates_default(self, store: SshConfigStore, profile: dict) -> None:
        await store.save(profile)
        assert store.get_default_name() == "web"
        await store.save({**profile, "name": "web2"}, rename_from="web")
        assert store.get_default_name() == "web2"

    async def test_rename_missing_source_raises(self, store: SshConfigStore, profile: dict) -> None:
        with pytest.raises(ValueError):
            await store.save(profile, rename_from="ghost")


class TestDelete:
    async def test_delete(self, store: SshConfigStore, profile: dict) -> None:
        await store.save(profile)
        assert await store.delete("web") is True
        assert store.get("web") is None

    async def test_delete_missing_returns_false(self, store: SshConfigStore) -> None:
        assert await store.delete("ghost") is False

    async def test_delete_default_falls_back(self, store: SshConfigStore, profile: dict) -> None:
        await store.save(profile)
        await store.save({**profile, "name": "db", "host": "10.0.0.2"})
        await store.set_default("web")
        await store.delete("web")
        assert store.get_default_name() == "db"


class TestPersistence:
    async def test_env_ref_preserved_raw(self, tmp_path) -> None:
        """密码中的 ${ENV_VAR} 引用须原样持久化，不展开。"""
        path = str(tmp_path / "connections.json")
        os.environ["SSH_TEST_PW"] = "expanded_value"
        try:
            s1 = SshConfigStore(path)
            await s1.save({
                "name": "env", "host": "h", "username": "u",
                "password": "${SSH_TEST_PW}",
            })
            # 重新加载验证文件中保留原始引用语法
            s2 = SshConfigStore(path)
            got = s2.get("env")
            assert got is not None
            assert got["password"] == "${SSH_TEST_PW}"
        finally:
            del os.environ["SSH_TEST_PW"]

    async def test_file_permission_600(self, tmp_path, profile: dict) -> None:
        path = str(tmp_path / "connections.json")
        s = SshConfigStore(path)
        await s.save(profile)
        mode = os.stat(path).st_mode & 0o777
        assert mode == 0o600

    async def test_reload_from_disk(self, tmp_path, profile: dict) -> None:
        path = str(tmp_path / "connections.json")
        s1 = SshConfigStore(path)
        await s1.save(profile)
        s2 = SshConfigStore(path)
        assert s2.get("web") is not None
        assert s2.get_default_name() == "web"
