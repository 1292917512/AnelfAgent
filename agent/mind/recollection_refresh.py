"""新增对话驱动的补充回忆，任务与当前回复共同收尾。

模型可见：新增对话对应的少量记忆及技能候选，经已有 provider 尾部提供。
Token：查询只取近期对话，Jev 只看短证据和未见候选；无新增事实零注入。
缓存：不改变稳定前缀、历史或工具链；未更新的补充快照保持字节稳定。
"""

from __future__ import annotations

import asyncio
import hashlib
from typing import TYPE_CHECKING, Any

from agent.context.observation import context_skills
from agent.judgment.evidence import conversation_evidence, dialogue_messages
from agent.judgment.gate import assess_optional_work
from agent.memory.memory_retriever import MemoryRetriever
from agent.memory.probe import deep_probe_hub
from agent.memory.probe_render import RecallDelta
from agent.memory.recall_fast import search_local
from agent.memory.recall_policy import current_recall_policy
from core.config import get_config_bool, get_config_float, get_config_int
from core.hooks import HookMode, hook_execution
from core.log import log
from core.sanitizer import truncate_middle
from core.tags import tag_label

if TYPE_CHECKING:
    from agent.mind.mind import Mind


class DialogueRecollection:
    """每回复一个工作者，只保留最新待查对话；旧结果不能越过对话修订。"""

    def __init__(self, mind: Mind, scope: str, conversation: list[dict[str, Any]]) -> None:
        self.mind = mind
        self.scope = scope
        self.state = deep_probe_hub.state_for(scope)
        self.task: asyncio.Task[None] | None = None
        self.pending: tuple[int, list[dict[str, Any]], str] | None = None
        self.seen = {self._key(conversation)}
        self.count = 0
        self.watermark: tuple[int, int] | None = None
        deep_probe_hub.record(scope, identifiers=(f"skill:{skill['name']}" for skill in context_skills(conversation)))

    @staticmethod
    def _key(conversation: list[dict[str, Any]]) -> str:
        text = next((message["content"] for message in reversed(dialogue_messages(conversation)) if message["role"] == "user"), "")
        return hashlib.sha256(" ".join(text.split()).casefold().encode()).hexdigest()

    def offer(self, conversation: list[dict[str, Any]], *, watermark: tuple[int, int]) -> None:
        """在真实用户消息并入后调用；仅调度内存快照，不等待检索。"""
        state = self.state
        if state is None or deep_probe_hub.state_for(self.scope) is not state or self.mind.retriever is None:
            return
        if watermark == self.watermark:
            return
        self.watermark = watermark
        policy = current_recall_policy(self.scope)
        if (not policy.background or policy.tags or policy.level not in {"fast", "deep"}
                or not get_config_bool("memory_probe_inject", True)
                or not get_config_bool("memory_probe_enabled", True)):
            return
        dialogue = dialogue_messages(conversation)
        dialogue = [message for message in dialogue if message["role"] == "user"
                    or (message.get("_source") or {}).get("origin") == "conversation"
                    or message.get("_layer") == "conversation"][-6:]
        key = self._key(dialogue)
        if key in self.seen or MemoryRetriever._is_trivial_turn(dialogue):
            return
        state.revision += 1
        self.pending = None
        if self.count >= max(0, min(10, get_config_int("memory_dialogue_refresh_limit", 3))):
            return
        self.seen.add(key)
        latest = next((index for index in range(len(dialogue) - 1, -1, -1) if dialogue[index]["role"] == "user"), None)
        if latest is None:
            return
        focus = MemoryRetriever._extract_focus_query([dialogue[latest]], max_chars=600)
        if not focus:
            return
        context = MemoryRetriever._extract_query(dialogue[:latest], max_chars=180)
        query = f"{focus}\n{context}" if context and context not in focus else focus
        self.pending = (state.revision, dialogue, query)
        if self.task is None or self.task.done():
            self.task = asyncio.create_task(self._work(), name=f"memory.dialogue.{self.scope}")
            state.task = self.task

    async def close(self) -> None:
        """回复结束即取消并回收工作者，不为迟到回忆另起模型调用。"""
        self.pending = None
        if self.state is not None and deep_probe_hub.state_for(self.scope) is self.state:
            deep_probe_hub.stop(self.scope)
        if self.task is not None:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)

    def _current(self, revision: int) -> bool:
        return (self.state is not None and deep_probe_hub.state_for(self.scope) is self.state
                and self.state.revision == revision)

    async def _work(self) -> None:
        while self.pending is not None:
            revision, dialogue, query = self.pending
            self.pending = None
            self.count += 1
            async with hook_execution("memory.dialogue", "context_refresh", HookMode.ASYNC, scope=self.scope) as run:
                try:
                    async with asyncio.timeout(max(0.1, get_config_float("memory_dialogue_refresh_timeout", 5))):
                        run.phase, run.operation = "retrieval", "根据新增对话并行检索本地记忆与技能"
                        await run.publish()
                        candidates = await self._candidates(query)
                        if not self._current(revision):
                            run.status, run.detail = "skipped", "对话已更新，未采用旧线索结果"
                            continue
                        candidates = deep_probe_hub.unseen(self.scope, candidates)
                        if not candidates:
                            run.detail = "没有需要补充的新事实"
                            continue
                        decision = await assess_optional_work(
                            "新增对话的补充回忆",
                            "判断这批候选是否直接帮助理解最新用户消息，或提供当前明确需要的方法。"
                            "仅主题相似、重复已知事实、旧状态冲突或身份不符均不应补充。"
                            "技能候选仅表示可阅读的方法，不代表已经执行；不能因为信息少就默认有用。",
                            {"scope": self.scope, "conversation": conversation_evidence(dialogue, max_chars=1400)},
                            candidates=[truncate_middle(delta.text, 400) for delta in candidates],
                        )
                        if self._current(revision) and decision.run:
                            if decision.selected is not None:
                                candidates = [candidates[index] for index in decision.selected]
                            candidates = deep_probe_hub.unseen(self.scope, candidates)
                            before = len(self.state.buffer.facts) if self.state else 0
                            deep_probe_hub.publish(self.scope, revision, candidates)
                            added = len(self.state.buffer.facts) - before if self.state else 0
                            run.detail = f"已准备 {added} 项补充资料；下次模型调用按上下文预算读取" if added else "补充快照预算已满或候选已覆盖，未增加内容"
                        else:
                            run.detail = decision.reason if self._current(revision) else "对话已更新，未采用旧线索结果"
                except TimeoutError:
                    run.status, run.detail = "skipped", "补充召回预算已用尽，主流程继续；主动召回不受影响"
                except Exception as exc:
                    run.status, run.detail = "error", f"{type(exc).__name__}: {exc}"
                    log(f"对话补充召回失败: {type(exc).__name__}: {exc}", "DEBUG", tag="记忆")

    async def _candidates(self, query: str) -> list[RecallDelta]:
        """复用现有检索和技能匹配，只返回短候选，不做规划或多级深探。"""
        retriever = self.mind.retriever
        if retriever is None:
            return []

        async def memory() -> list[RecallDelta]:
            from agent.memory.reflection_lifecycle import scope_tags_from_entity_scopes
            results = await search_local(retriever, query)
            owner_tags = set(scope_tags_from_entity_scopes([self.scope]))
            results = [result for result in results if result.sensitivity == "normal" or owner_tags.intersection(result.tags)]
            results = retriever._apply_scope_boost(results, [self.scope])[:3]
            return await deep_probe_hub._memory_deltas(results, retriever._store, "补充记忆")

        async def skills() -> list[RecallDelta]:
            if not self.mind._skills_enabled():
                return []
            matches = await self.mind.skill_matcher.match([query], top_k=2)
            deltas = []
            from agent.skills.dependencies import mcp_binding_notice
            for matched_skill, _score in matches:
                skill = self.mind.skill_store.get(matched_skill.name)
                if skill is None:
                    continue
                text = (f"{tag_label('skill', skill.name)} {skill.description[:160]}"
                        f"（{skill.state.value}；需要时 get_skill 读取，候选不代表执行）")
                notice = mcp_binding_notice(skill, self.scope)
                if notice:
                    text += f"\n{notice}"
                deltas.append(RecallDelta("相关技能候选", text, f"skill:{skill.name}", text))
            return deltas

        results = await asyncio.gather(memory(), skills(), return_exceptions=True)
        deltas: list[RecallDelta] = []
        for result in results:
            if isinstance(result, BaseException):
                if not isinstance(result, Exception):
                    raise result
                log(f"补充召回分支失败: {type(result).__name__}", "DEBUG", tag="记忆")
            else:
                deltas.extend(result)
        return deltas
