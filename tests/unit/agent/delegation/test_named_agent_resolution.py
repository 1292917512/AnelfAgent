"""子代理统一注册表的委托链路解析单元测试：agent_name 优先级 / 工具层校验。"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

from agent.delegation.delegate_tool import delegate_task, register_delegation_tools
from agent.delegation.delegation_manager import DelegationManager


class _StubLLMManager:
    """最小 LLMManager 替身：按预设表解析档案池与难度挡位。"""

    def __init__(self, profiles: dict[str, str], tiers: dict[int, str]) -> None:
        self._profiles = profiles
        self._tiers = tiers

    def list_sub_agents(self) -> list[dict]:
        return [
            {"name": n, "models": [m], "description": "", "tier": 0,
             "builtin": False, "first_available": m,
             "model_missing": False, "model_enabled": True}
            for n, m in self._profiles.items()
        ]

    def resolve_sub_agent_model(self, name: str):
        return self._profiles.get(name)

    def resolve_delegation_model(self, difficulty):
        return self._tiers.get(int(difficulty or 0))


class _FakeMind:
    def __init__(self, output: str = "子任务完成") -> None:
        self.reflect = AsyncMock(return_value=output)
        self.llm_manager: _StubLLMManager | None = None

    def get_model_context_length(self) -> int:
        return 128_000


def _make_manager(profiles=None, tiers=None) -> tuple[DelegationManager, _StubLLMManager]:
    mind = _FakeMind()
    stub = _StubLLMManager(profiles or {}, tiers or {})
    mind.llm_manager = stub
    return DelegationManager(mind), stub


class TestResolvePriority:
    async def test_agent_name_wins_over_difficulty(self) -> None:
        manager, _ = _make_manager(
            profiles={"researcher": "glm-flash"}, tiers={2: "tier2-model"},
        )
        assert manager._resolve_model("researcher", 2) == "glm-flash"

    async def test_difficulty_used_without_agent_name(self) -> None:
        manager, _ = _make_manager(profiles={"r": "m"}, tiers={3: "tier3-model"})
        assert manager._resolve_model("", 3) == "tier3-model"

    async def test_unknown_agent_falls_back_to_difficulty_then_default(self) -> None:
        manager, _ = _make_manager(profiles={}, tiers={1: "tier1-model"})
        # 档案未命中 → 难度挡位兜底（工具层已前置拦截未知名称，此处为管理层防御）
        assert manager._resolve_model("ghost", 1) == "tier1-model"
        assert manager._resolve_model("ghost", 0) == ""

    async def test_delegate_resolves_profile_model(self) -> None:
        manager, _ = _make_manager(profiles={"researcher": "glm-flash"})
        result = await manager.delegate("调研 X", agent_name="researcher")
        assert result.success
        # 反思调用携带 _model_id 覆盖（与 TaskExecutor 同管道）
        kwargs = manager._mind.reflect.call_args.kwargs
        assert kwargs["options"] == {"_model_id": "glm-flash"}

    async def test_batch_per_item_agent_override(self) -> None:
        manager, _ = _make_manager(
            profiles={"researcher": "glm-flash"}, tiers={1: "tier1-model"},
        )
        tasks = [
            {"goal": "A", "agent": "researcher"},
            {"goal": "B", "difficulty": 1},
        ]
        results = await manager.delegate_batch(tasks)
        assert all(r.success for r in results)
        # 并发执行下调用顺序不定，按 options 取值集合断言两种模型各命中一次
        options_used = {
            json.dumps(c.kwargs.get("options"), ensure_ascii=False)
            for c in manager._mind.reflect.call_args_list
        }
        assert options_used == {'{"_model_id": "glm-flash"}', '{"_model_id": "tier1-model"}'}


class TestDelegateToolValidation:
    async def test_unknown_agent_returns_param_error(self) -> None:
        manager, _ = _make_manager(profiles={"researcher": "glm-flash"})
        register_delegation_tools(manager)
        out = json.loads(await delegate_task(goal="做调研", agent_name="ghost"))
        assert "不存在" in json.dumps(out, ensure_ascii=False)
        # 附可用名称列表供自纠正
        assert "researcher" in json.dumps(out, ensure_ascii=False)

    async def test_disabled_profile_model_rejected(self) -> None:
        manager, _ = _make_manager(profiles={"researcher": "glm-flash"})
        # 覆写档案可用性：候选池无可用模型
        manager._mind.llm_manager.list_sub_agents = lambda: [{
            "name": "researcher", "models": ["glm-flash"], "description": "",
            "tier": 0, "builtin": False, "first_available": None,
            "model_missing": False, "model_enabled": False,
        }]
        register_delegation_tools(manager)
        out = json.loads(await delegate_task(goal="做调研", agent_name="researcher"))
        assert "无可用模型" in json.dumps(out, ensure_ascii=False)

    async def test_valid_agent_proceeds(self) -> None:
        manager, _ = _make_manager(profiles={"researcher": "glm-flash"})
        register_delegation_tools(manager)
        out = json.loads(await delegate_task(goal="做调研", agent_name="researcher"))
        assert out["total"] == 1
        assert out["succeeded"] == 1
