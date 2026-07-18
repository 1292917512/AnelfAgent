"""技能后台评审（参考 hermes-agent background_review）。

每轮对话结束后，spawn 后台任务评审本轮执行摘要：
询问 LLM "这段经验中是否有可复用的方法/流程值得保存为技能"，
由 LLM 自主调用 create_skill / update_skill 完成写入（不影响主对话）。

防失控设计：
- 上一次评审未完成时跳过本次（不堆积）
- 评审使用受限工具集（仅 skills 组），禁止外发消息
- 评审轮次上限小（默认 4 轮），无价值时 LLM 直接 end_reply
"""
from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Optional

from core.event_bus import EVENT_AFTER_REPLY, event_bus
from core.log import log

from agent.skills.skill_store import SkillStore

if TYPE_CHECKING:
    from agent.mind.mind import Mind

_REVIEW_PROMPT = """你是技能评审员。以下是刚完成的一轮对话执行摘要：

{summary}

现有技能库：
{existing}

请判断：这段经验中是否有**可复用的方法、流程或知识**值得保存为技能？
- 如有新经验：调用 create_skill 创建（内容要是通用方法，而非一次性具体内容）
- 如已有技能可改进：调用 update_skill 增量更新
- 如没有价值：直接调用 end_reply 结束，不要做多余操作
"""

_MAX_REVIEW_ITERATIONS = 4
_MAX_SUMMARY_CHARS = 3000


class SkillReviewer:
    """技能后台评审器：对话结束后评审经验并沉淀技能。"""

    def __init__(self, mind: "Mind", store: SkillStore) -> None:
        self._mind = mind
        self._store = store
        self._task: Optional[asyncio.Task] = None
        self._started = False

    def start(self) -> None:
        """订阅回复完成事件（幂等）。"""
        if self._started:
            return
        event_bus.on(EVENT_AFTER_REPLY, self._on_after_reply, owner="skills.review")
        self._started = True
        log("技能后台评审已启动", "DEBUG", tag="技能")

    def stop(self) -> None:
        """停止评审（取消订阅与进行中的任务）。"""
        event_bus.off_by_owner("skills.review")
        if self._task and not self._task.done():
            self._task.cancel()
        self._started = False

    @staticmethod
    def _enabled() -> bool:
        from core.config import get_config_bool
        return get_config_bool("skills_review_enabled", True)

    async def _on_after_reply(self, payload: dict) -> None:
        """回复完成后触发后台评审（不阻塞主流程）。"""
        if not self._enabled():
            return
        if payload.get("error"):
            return
        if self._task and not self._task.done():
            log("上一次技能评审未完成，跳过本次", "DEBUG", tag="技能")
            return
        self._task = asyncio.create_task(self._review(), name="skills.review")

    async def _review(self) -> None:
        """执行评审：用受限工具集让 LLM 自主决定是否沉淀技能。"""
        try:
            # 评审材料：短期记忆中的执行摘要（finish_think 写入）
            clips = self._mind.pfc.temporary
            if not clips:
                return
            summary = "\n".join(
                str(c.get("content", "")) for c in clips[-5:]
            )[:_MAX_SUMMARY_CHARS]
            if not summary.strip():
                return

            existing_skills = self._store.list_skills()
            existing = (
                "\n".join(f"- {s.name}: {s.description}" for s in existing_skills[:20])
                or "（空）"
            )

            prompt = _REVIEW_PROMPT.format(summary=summary, existing=existing)
            log("技能后台评审开始", "DEBUG", tag="技能")
            await self._mind.reflect(
                [{"role": "user", "content": prompt}],
                max_iterations=_MAX_REVIEW_ITERATIONS,
                tool_tags=["skills"],
                allow_output_tools=False,
            )
            log("技能后台评审完成", "DEBUG", tag="技能")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log(f"技能后台评审失败: {type(exc).__name__}: {exc}", "WARNING", tag="技能")
