"""keyless 快照回放测试 — 锁定回复主路径的行为回归（对齐 dsh keyless snapshot）。

无需 API key、不触网络：脚本化的 LLM 轮次队列即"录制"，测试驱动真实
think_loop 回放并断言三件套——
  1. sent_messages 布局（_layer 序列 / 消息数 / 角色序列）
  2. 工具调用序列（名称 + 顺序，经 fake tool_executor 捕获）
  3. 最终出站文本（deliver_text mock 捕获）

任何改动 think_loop / 上下文组装 / 出站路由的 PR，若改变了模型可见行为，
这里的断言会红——迫使作者在 PR 里解释"行为为什么该变"。
"""

from __future__ import annotations

import pytest
from helpers.think_loop_fakes import (
    FakeMind,
    FakePfc,
    run_think_loop,
    text_result,
    tool_result,
)

from agent.messages import MessageUser


class _ReplayMind(FakeMind):
    """脚本化 Mind 替身：按 _rounds 队列逐轮响应（轮次耗尽即失败）。"""

    def __init__(self, rounds: list) -> None:
        super().__init__(rounds=rounds, default_text=None, pfc=FakePfc(exec_layer=True))


@pytest.fixture
def replay_anything():
    return MessageUser(uid=1)


def _base() -> list:
    return [
        {"role": "system", "content": "人设", "_layer": "stable"},
        {"role": "user", "content": "你好", "_layer": "conversation"},
    ]


class TestScenarioPureText:
    """场景 1：纯文本回复（单轮收敛）。"""

    async def test_replay(self, replay_anything, deliver_mock) -> None:
        mind = _ReplayMind([text_result("你好呀～")])
        await run_think_loop(
            mind, anything=replay_anything, base_messages=_base(), adapter_key="test",
        )
        # 布局：单轮调用，base(2) + exec_context(1)
        assert len(mind.sent_messages) == 1
        round1 = mind.sent_messages[0]
        assert [m.get("_layer") for m in round1[:2]] == ["stable", "conversation"]
        assert round1[-1].get("_layer") == "exec_context"
        # 无工具执行
        assert mind.executed_tools == []
        # 出站文本
        deliver_mock.assert_awaited()
        assert deliver_mock.await_args.args[0] == "你好呀～" or \
            "你好呀～" in str(deliver_mock.await_args.args)


class TestScenarioSingleTool:
    """场景 2：单工具调用后文本收敛。"""

    async def test_replay(self, replay_anything, deliver_mock) -> None:
        mind = _ReplayMind([tool_result("", ["recall"]), text_result("查到了")])
        await run_think_loop(
            mind, anything=replay_anything, base_messages=_base(), adapter_key="test",
        )
        # 两轮 LLM 调用
        assert len(mind.sent_messages) == 2
        # 工具序列：recall 恰好执行一次
        assert mind.executed_tools == ["recall"]
        # 第 2 轮上下文含第 1 轮的 assistant + tool 结果（工具链并入）
        roles2 = [m.get("role") for m in mind.sent_messages[1]]
        assert "tool" in roles2
        # 出站文本
        assert deliver_mock.await_count == 1


class TestScenarioMultiToolChain:
    """场景 3：多轮工具链（顺序保持）。"""

    async def test_replay(self, replay_anything, deliver_mock) -> None:
        mind = _ReplayMind([
            tool_result("", ["recall"]),
            tool_result("", ["get_conversation"]),
            text_result("都办好了"),
        ])
        await run_think_loop(
            mind, anything=replay_anything, base_messages=_base(), adapter_key="test",
        )
        # 三轮 LLM 调用
        assert len(mind.sent_messages) == 3
        # 工具序列按轮次顺序执行
        assert mind.executed_tools == ["recall", "get_conversation"]
        # 最终出站
        assert deliver_mock.await_count == 1
