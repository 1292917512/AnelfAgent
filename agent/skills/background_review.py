"""技能后台评审（参考 hermes-agent background_review）。

每轮对话结束后，spawn 后台任务评审本轮执行摘要，由 LLM 自主决策是否沉淀/
合并/治理技能（不影响主对话）。

设计定位（决策导向，非禁止导向）：评审的上下文由事实层（SkillIndex）供给——
语义相近候选 + 库健康摘要。过去的失败不是 LLM 不会判断，而是它看不见库
（只给最近活动的 20 条）；把现状算清楚呈现给它，判断交给它。

评审材料契约：读取 EVENT_AFTER_REPLY.execution_summary
（由 finish_think → complete_reply 写入），不依赖 pfc.temporary。

防失控设计：
- 上一次评审未完成时跳过本次（不堆积）
- 评审使用受限工具集（仅 skills 组），禁止外发消息
- 评审轮次上限小（默认 6 轮，含决策协议的回执往返），无价值时 LLM 直接 end_reply
"""
from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Optional

from agent.skills.skill_index import SkillIndex
from agent.skills.skill_store import SkillStore
from core.event_bus import EVENT_AFTER_REPLY, event_bus
from core.log import log

if TYPE_CHECKING:
    from agent.mind.mind import Mind

_REVIEW_PROMPT = """你是技能评审员，负责技能库的沉淀与治理。刚完成了一轮对话：

## 本轮执行摘要
{summary}

## 语义相近的现有技能（合并优先考察对象）
{candidates}

## 技能库现状
{health}

## 评审四问（全部为"是"才值得沉淀）
1. **可复用**：去掉具体人名/账号/IP/时间后，方法或流程仍适用吗
2. **非一次性**：未来遇到相似任务时能用上吗
3. **归属正确**：用户偏好/事实数据 → memorize；环境快照（连接方式/部署清单/端口/IP）→
   memory 或 notes；行为准则（"必须调某工具""输出别带某标签"）→ 不是检索型技能；
   只有**任务方法/流程/排障经验**才是技能
4. **粒度合适**：覆盖一类任务（如"网络调研""SQL 排错"），不是单次步骤记录

## 决策路径（按顺序考察）
1. 无可沉淀 → 直接 end_reply（多数轮次如此，宁缺毋滥）
2. 相近技能可容纳本次经验 → **合并优先**：update_skill 增量补充（删除过时部分，
   不要只增不减），或对库现状中的合并候选用 merge_skills 显式合并
3. 确有差异需新建 → create_skill；若返回"需要决策"的诊断报告（相近技能/触发词碰撞），
   阅读事实后自行裁决：合并 / 带 decision 参数（差异理由）重呼确认 / 放弃
4. 库现状中的问题技能（零参与/高匹配零消费/触发词互相竞争/高频改写未收敛/
   解析失败）可顺手治理：merge / update 收敛 / create_skill 同名重建修复 / 不管（有理由地保留）

## 写作与召回
- 正文：步骤 + 坑 + 注意事项，markdown 列表化，控制在 2000 字内，能列表别流水
- **禁止**写：结果性内容（"帮某人查到了 X"）、流水账、AI 自述
- 触发词：3~8 个高区分度词，宁少勿泛；只在真实漏召回后补充，不做预防性堆砌
  （工具会报告跨技能碰撞的泛词，此时换更具体的表述或合并竞争技能）
- 本轮最多一次写入（create / update / merge 三选一）

## 工作原则
- 多数轮次都是无价值的，先看相近候选再下结论，不一拍脑门写新技能
- 技能库是有限容量的资产：每次写入都在花库容，合并与删除同样是贡献
- **治理操作必须走技能工具**（create_skill / update_skill / merge_skills），
  禁止直接用文件工具编辑 SKILL.md——手写 frontmatter 会破坏格式契约
"""

_MAX_REVIEW_ITERATIONS = 6
_MAX_SUMMARY_CHARS = 3000
_MAX_CANDIDATES = 10


class SkillReviewer:
    """技能后台评审器：对话结束后评审经验，自主决策沉淀与治理。"""

    def __init__(self, mind: "Mind", store: SkillStore, index: Optional[SkillIndex] = None) -> None:
        self._mind = mind
        self._store = store
        self._index = index or SkillIndex(store)
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

    async def _build_candidates(self, summary: str) -> str:
        """语义相近技能候选（评审查重的感知基础，无 Embedder 时降级为高频技能）。"""
        try:
            similar = await self._index.similar(text=summary, top_k=_MAX_CANDIDATES)
            if similar:
                return "\n".join(
                    f"- {s.name}（相似度 {sim:.2f}，use={s.use_count}/match={s.match_count}/"
                    f"patch={s.patch_count}）: {s.description}"
                    for s, sim in similar
                )
        except Exception as exc:
            log(f"评审候选检索失败: {exc}", "DEBUG", tag="技能")
        # 降级：按真实使用倒序取头部（库小时与语义路等价，库大时提示感知受限）
        top = sorted(
            self._store.list_skills(),
            key=lambda s: (s.use_count, s.last_activity_at), reverse=True,
        )[:_MAX_CANDIDATES]
        if not top:
            return "（库为空）"
        return "\n".join(
            f"- {s.name}（use={s.use_count}/match={s.match_count}/patch={s.patch_count}）: {s.description}"
            for s in top
        ) + "\n（语义检索不可用，以上为使用频率降级列表）"

    def _build_health(self) -> str:
        """库健康摘要（来自事实层快照，fail-open：失败时给最小事实）。"""
        try:
            snapshot = self._index.snapshot()
        except Exception:
            return "（健康快照不可用）"
        counts = snapshot["counts"]
        lines = [
            f"库容 {counts['active']} active / {counts['stale']} stale"
            f"（参考水位 {snapshot['capacity_reference']}）",
        ]
        if snapshot["parse_errors"]:
            rendered = "; ".join(
                f"{name}（{err[:60]}）"
                for name, err in list(snapshot["parse_errors"].items())[:5]
            )
            lines.append(
                f"解析失败（frontmatter 被外部写脏，用文件工具读取原文后 "
                f"create_skill 同名重建即可修复）: {rendered}"
            )
        if snapshot["zero_engagement"]:
            lines.append(f"零参与（无使用且无匹配）: {', '.join(snapshot['zero_engagement'][:10])}")
        if snapshot["high_match_low_use"]:
            names = ", ".join(x["name"] for x in snapshot["high_match_low_use"][:10])
            lines.append(f"高匹配零消费（疑似冗余/已内化）: {names}")
        if snapshot["trigger_collisions"]:
            top_collisions = list(snapshot["trigger_collisions"].items())[:5]
            rendered = "; ".join(f"'{p}'×{len(ns)}" for p, ns in top_collisions)
            lines.append(f"触发词碰撞: {rendered}")
        if snapshot["merge_signals"]:
            top_signals = snapshot["merge_signals"][:5]
            rendered = "; ".join(f"{x['folded']}→{x['kept']}(×{x['count']})" for x in top_signals)
            lines.append(f"检索折叠合并信号: {rendered}")
        return "\n".join(lines)

    async def _review(self, summary: str) -> None:
        """执行评审：感知完备的上下文 + 受限工具集，由 LLM 自主决策。"""
        try:
            summary = summary[:_MAX_SUMMARY_CHARS]
            candidates = await self._build_candidates(summary)
            health = self._build_health()

            prompt = _REVIEW_PROMPT.format(
                summary=summary, candidates=candidates, health=health,
            )
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
