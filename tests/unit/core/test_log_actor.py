"""core.log 日志 actor 上下文（执行主体归因：主 AI vs 子代理）单元测试。"""

from __future__ import annotations

import asyncio
from typing import List

from core import log as log_mod
from core.log import bind_log_actor, current_log_actor, query_log_buffer, reset_log_actor


def _recent_messages(limit: int = 10) -> List[str]:
    return [str(r["message"]) for r in query_log_buffer(limit=limit)]


class TestLogActor:
    def test_prefix_applied_and_reset(self) -> None:
        token = bind_log_actor("子代理@leaf#abc123")
        try:
            log_mod.log("actor冒烟-绑定中", tag="委托")
            assert current_log_actor() == "子代理@leaf#abc123"
        finally:
            reset_log_actor(token)
        log_mod.log("actor冒烟-复位后")
        msgs = _recent_messages()
        assert "[子代理@leaf#abc123] actor冒烟-绑定中" in msgs
        assert "actor冒烟-复位后" in msgs
        assert current_log_actor() == ""

    def test_nested_binding_innermost_wins(self) -> None:
        outer = bind_log_actor("外层actor")
        inner = bind_log_actor("内层actor")
        try:
            log_mod.log("actor冒烟-嵌套")
        finally:
            reset_log_actor(inner)
            reset_log_actor(outer)
        assert "[内层actor] actor冒烟-嵌套" in _recent_messages()
        assert current_log_actor() == ""

    def test_unbound_zero_prefix(self) -> None:
        log_mod.log("actor冒烟-无绑定")
        assert "actor冒烟-无绑定" in _recent_messages()

    async def test_contextvar_propagates_into_tasks(self) -> None:
        token = bind_log_actor("子代理@leaf#xyz999")
        try:
            async def work() -> None:
                log_mod.log("actor冒烟-任务内")

            await asyncio.create_task(work())
        finally:
            reset_log_actor(token)
        assert "[子代理@leaf#xyz999] actor冒烟-任务内" in _recent_messages()
