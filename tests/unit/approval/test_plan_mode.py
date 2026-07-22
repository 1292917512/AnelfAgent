"""B5 自发 Plan 模式测试：present_plan 工具 + 默认 ask 规则 + 批准闭环。"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from agent.approval.gate import ApprovalGate
from agent.approval.rules import PermissionDecision, default_rules
from agent.approval.session import ApprovalDecision


class TestDefaultRules:
    def test_present_plan_ask_by_default(self):
        rs = default_rules()
        v = rs.evaluate("present_plan", {"goal": "重构模块"}, "webui", "u1")
        assert v.decision == PermissionDecision.ASK

    def test_other_tools_still_allow(self):
        rs = default_rules()
        v = rs.evaluate("read_file", {"path": "a"}, "webui", "u1")
        assert v.decision == PermissionDecision.AUTO_ALLOW


class TestPlanApprovalFlow:
    async def test_plan_approved_proceeds(self):
        gate = ApprovalGate(rule_set=default_rules())
        prompts = []

        async def _render(ctx):
            prompts.append(ctx)
            return SimpleNamespace(channel=SimpleNamespace(channel_id=""))

        async def _forward(req):
            return SimpleNamespace(success=True, error="")

        ch = SimpleNamespace(
            channel_id="webui",
            send_text=lambda *a, **k: asyncio.sleep(0),
            render_approval_prompt=_render,
            forward_message=_forward,
        )

        async def decide():
            await asyncio.sleep(0.2)
            pending = await gate._manager.list_pending()
            assert len(pending) == 1
            # 计划内容完整呈现在批准请求中（2000 字符截断上限）
            assert "重构模块" in str(pending[0].request.tool_args)
            await gate.approve(pending[0].request.request_id, decided_by="user")

        task = asyncio.create_task(decide())
        decision = await gate.request_approval(
            tool_name="present_plan",
            tool_args={"goal": "重构模块", "steps": "1.分析 2.实施 3.验证"},
            reason="Agent 自发提交计划",
            channel=ch, chat_id="c", user_id="u", timeout=5,
        )
        await task
        assert decision == ApprovalDecision.APPROVED
        assert len(prompts) == 1

    async def test_plan_denied_agent_notified(self):
        gate = ApprovalGate(rule_set=default_rules())
        async def _render(ctx):
            return SimpleNamespace(channel=SimpleNamespace(channel_id=""))

        async def _forward(req):
            return SimpleNamespace(success=True, error="")

        ch = SimpleNamespace(
            channel_id="webui",
            send_text=lambda *a, **k: asyncio.sleep(0),
            render_approval_prompt=_render,
            forward_message=_forward,
        )

        async def decide():
            await asyncio.sleep(0.2)
            pending = await gate._manager.list_pending()
            await gate.deny(pending[0].request.request_id, decided_by="user", reason="先不做第 2 步")

        task = asyncio.create_task(decide())
        decision = await gate.request_approval(
            tool_name="present_plan", tool_args={"goal": "x"},
            reason="t", channel=ch, chat_id="c", user_id="u", timeout=5,
        )
        await task
        assert decision == ApprovalDecision.DENIED


class TestPresentPlanTool:
    async def test_tool_returns_approved_message(self):
        from agent.planning.tools import present_plan
        out = json.loads(await present_plan(goal="目标", steps="1.a\n2.b"))
        assert out["ok"] and out["approved"]
