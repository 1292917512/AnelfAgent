"""tools 层测试：AI 总开关门控 / 解锁态门控 / 风险标记 / 错误归因。"""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

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
    @pytest.mark.parametrize("approved", [True, False])
    async def test_reveal_permission_to_model_and_redacted_records(self, service, monkeypatch, approved):
        from agent.approval.types import ApprovalResult
        from agent.llm.types import ChatResult, ToolCall
        from agent.mind.execution.reply_finalize import _build_execution_summary, complete_reply
        from agent.mind.execution.reply_journal import ReplyToolJournal
        from agent.mind.execution.tool_execution import execute_tool_calls
        from core.config import ConfigManager
        from core.event_bus import EVENT_AFTER_REPLY, EVENT_THINKING_TOOL_END, EVENT_TRACE_CALL_END, event_bus
        from core.tool_results import redact_tool_messages

        ConfigManager.set("security_sanitize_enabled", True)
        password = "sk-abcdefghijklmnop123456789012"
        entry = await service.add_entry(title="Audit", password=password)
        arguments = json.dumps({"entry_id": entry["id"]})
        call = ToolCall("reveal-call", "vault_reveal", arguments,
                        ToolCall.wire_raw("reveal-call", "vault_reveal", arguments))
        gate = SimpleNamespace(check=AsyncMock(return_value=ApprovalResult(
            approved, "guardian_approved" if approved else "guardian_denied", "decision")))
        monkeypatch.setattr("agent.approval.get_approval_gate", lambda: gate)
        events = AsyncMock()
        monkeypatch.setattr(event_bus, "emit", events)
        sink = SimpleNamespace(append_reply_tool_row=AsyncMock())
        mind = SimpleNamespace(set_phase=lambda phase: None, memory_store=None,
                               model_invoker=SimpleNamespace(get_context_length=lambda: 0))
        chain = []
        await execute_tool_calls(mind, chain, ChatResult(content=""), [call], 0,
                                 journal=ReplyToolJournal(sink, "audit-scope"))
        gate.check.assert_awaited_once()
        result = json.loads(chain[-1]["content"])
        if approved:
            assert result["password"] == password
            assert "临时使用" in result["notice"] and "外发" in result["notice"]
            assert "禁止写入记忆" in result["notice"]
        else:
            assert result["permission_outcome"] == "guardian_denied"
            assert "password" not in result
        observed = [event.args[1] for event in events.await_args_list
                    if event.args[0] in {EVENT_THINKING_TOOL_END, EVENT_TRACE_CALL_END}]
        assert password not in json.dumps(observed, ensure_ascii=False)
        assert password not in str(sink.append_reply_tool_row.call_args)
        assert password not in _build_execution_summary(chain, [])
        assert password not in json.dumps(redact_tool_messages(chain), ensure_ascii=False)
        await complete_reply(mind, None, "", 1, tool_chain=chain, completion={"messages": chain})
        after_reply = next(event.args[1] for event in events.await_args_list if event.args[0] == EVENT_AFTER_REPLY)
        assert password not in json.dumps(after_reply["messages"], ensure_ascii=False)
        assert (password in chain[-1]["content"]) is approved

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
