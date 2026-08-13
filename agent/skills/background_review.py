"""技能后台评审（参考 hermes-agent background_review）。

每轮对话结束后，spawn 后台任务评审本轮执行摘要：
询问 LLM "这段经验中是否有可复用的方法/流程值得保存为技能"，
由 LLM 自主调用 create_skill / update_skill 完成写入（不影响主对话）。

评审材料契约：读取 EVENT_AFTER_REPLY.execution_summary
（由 finish_think → complete_reply 写入），不依赖 pfc.temporary。

防失控设计：
- 上一次评审未完成时跳过本次（不堆积）
- 评审使用受限工具集（仅 skills 组），禁止外发消息
- 评审轮次上限小（默认 4 轮），无价值时 LLM 直接 end_reply
"""
from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Optional

from agent.skills.skill_store import SkillStore
from core.event_bus import EVENT_AFTER_REPLY, event_bus
from core.log import log

if TYPE_CHECKING:
    from agent.mind.mind import Mind

_REVIEW_PROMPT = """你是技能评审员。刚完成了一轮对话：

## 本轮执行摘要
{summary}

## 已有技能库（按使用频率倒序，最多 20 条）
{existing}

## 沉淀判据（全部满足才写技能）
1. **可复用**：去掉具体人名/账号/时间后，方法或流程仍适用 → 是 / 否
2. **非一次性**：未来遇到相似任务时能用上 → 是 / 否
3. **非偏好**：用户偏好、事实数据走 memorize/pin，**不要**写技能
4. **粒度合适**：覆盖一类任务（如"网络调研""SQL 排错"），不是单条步骤记录

四条全过 → 写技能；任一不满足 → 直接 end_reply。

## 三步流程
1. 先读：用 `get_skill` 查看相近技能现有内容（避免盲覆盖）
2. 再写：
   - 全新 → `create_skill`，**必填** `description`（一句话场景+目的）+ **至少 3 个** `trigger_patterns`（同义词/相关词，召回生命线）
   - 已有 → `update_skill` 增量补充（保留可复用部分，不要整篇覆盖）
3. 写完调 `end_reply` 结束，不要做多余操作

## 无价值情形（直接 end_reply）
- 只调了工具、没沉淀方法
- 一次性具体值（某人电话/某条 ID）
- 与已有技能重复且无新增信息

## 写作要求
- **方法**写法：步骤 + 注意事项 + 常见坑（用 markdown 列）
- **禁止**写：结果性内容（"帮张三查到了 X"）、流水账、AI 自述
- 长度控制在 800 字内，能列表别流水

## 工作原则
- 多数轮次都是无价值的，宁缺毋滥
- 拿不准时，看一遍相近技能再决定，不要一拍脑门写新技能
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
        summary = str(payload.get("execution_summary") or "").strip()
        if not summary:
            return
        if self._task and not self._task.done():
            log("上一次技能评审未完成，跳过本次", "DEBUG", tag="技能")
            return
        self._task = asyncio.create_task(
            self._review(summary), name="skills.review",
        )

    async def _review(self, summary: str) -> None:
        """执行评审：用受限工具集让 LLM 自主决定是否沉淀技能。"""
        try:
            summary = summary[:_MAX_SUMMARY_CHARS]
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
