"""技能后台评审（参考 hermes-agent background_review）。

每轮对话结束后，经 LLM 钩子面（agent/hooks_llm）派生一个后台评审任务，
继承本轮完整上下文（transcript），由 LLM 自主决策是否沉淀/合并/治理技能
（异步并行，不影响主对话与下一轮）。

设计定位（决策导向，非禁止导向）：评审的上下文由事实层（SkillIndex）供给——
语义相近候选 + 库健康摘要。过去的失败不是 LLM 不会判断，而是它看不见库
（只给最近活动的 20 条）；把现状算清楚呈现给它，判断交给它。

评审材料契约：钩子面以 transcript 档位带入本轮完整消息链（base + tool_chain），
替代旧版仅 ~3K 字符的执行摘要——工具结果细节不再被摘要蒸馏丢弃，"任务方法/
排障经验"类技能的沉淀判断材料更完整。transcript 快照经 hooks_llm 护栏截断
（保头保尾），成本受 hooks_llm_transcript_max_chars 约束。

防失控设计（钩子面治理 + 本层语义）：
- 上一轮评审未完成时跳过本次（钩子 per-hook 并发=1，单飞不堆积）
- 评审使用受限工具集（仅 skills 组），禁止外发消息
- 评审轮次上限小（默认 6 轮，含决策协议的回执往返），无价值时 LLM 直接 end_reply
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from agent.hooks_llm import HookContext, HookRegistry, llm_hook
from agent.skills.skill_index import SkillIndex
from agent.skills.skill_store import SkillStore
from core.log import log

if TYPE_CHECKING:
    from agent.mind.mind import Mind

_HOOK_NAME = "skill_review"
_HOOK_OWNER = "skills.review"

_REVIEW_INSTRUCTION = """以上是本轮对话的完整执行上下文（你的回复、工具调用与结果原文）。

你现在是技能评审员，负责技能库的沉淀与治理。请基于上方真实执行过程评审：

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
_MAX_CANDIDATES = 10
# 评审触发最小材料：transcript 快照至少包含非空消息链才评审
_MIN_TRANSCRIPT_MESSAGES = 2


class SkillReviewer:
    """技能后台评审器：经钩子面注册评审钩子，继承完整上下文异步评审。"""

    def __init__(self, mind: "Mind", store: SkillStore, index: Optional[SkillIndex] = None) -> None:
        self._mind = mind
        self._store = store
        self._index = index or SkillIndex(store)
        self._started = False

    def start(self) -> None:
        """经钩子面注册评审钩子（幂等）。

        不再自订阅 EVENT_AFTER_REPLY——钩子面统一负责事件订阅、并行拉起、
        防递归与治理；本类只提供评审的事实供给与执行体。
        """
        if self._started:
            return
        if not self._enabled():
            return
        self._register_hook()
        self._started = True
        log("技能后台评审已启动（经 LLM 钩子面）", "DEBUG", tag="技能")

    def stop(self) -> None:
        """停止评审（注销钩子）。"""
        HookRegistry.unregister(_HOOK_NAME)
        self._started = False

    @staticmethod
    def _enabled() -> bool:
        from core.config import get_config_bool
        return get_config_bool("skills_review_enabled", True)

    def _register_hook(self) -> None:
        """注册 skill_review 钩子（transcript 档位 + 条件门控）。"""
        reviewer = self

        def _when(payload: dict) -> bool:
            # 无错误 + transcript 快照带出真实消息链才评审
            if payload.get("error"):
                return False
            messages = payload.get("messages")
            return isinstance(messages, list) and len(messages) >= _MIN_TRANSCRIPT_MESSAGES

        @llm_hook(
            _HOOK_NAME, event="after_reply", context="transcript",
            when=_when, tool_tags=["skills"], allow_output_tools=False,
            max_iterations=_MAX_REVIEW_ITERATIONS, max_concurrent=1,
            owner=_HOOK_OWNER, source="code",
            description="每轮对话后评审执行过程，自主决策技能沉淀/合并/治理",
        )
        async def _skill_review_hook(ctx: HookContext) -> Optional[str]:
            return await reviewer._run(ctx)

    async def _build_candidates(self, seed_text: str) -> str:
        """语义相近技能候选（评审查重的感知基础，无 Embedder 时降级为高频技能）。"""
        try:
            similar = await self._index.similar(text=seed_text, top_k=_MAX_CANDIDATES)
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

    async def _run(self, ctx: HookContext) -> Optional[str]:
        """评审执行体：transcript 快照 + 评审指令 → reflect 自主决策。

        快照即 base_messages（人设/stable 前缀 + 本轮完整执行过程），评审指令
        以 user 角色追加在尾部——模型在「刚做完这轮」的语境里直接评审。
        返回产出文本供钩子面登记（None/空 = 无沉淀）。
        """
        if not ctx.transcript_available():
            return None
        execution_summary = str(ctx.payload.get("execution_summary") or "")
        seed = execution_summary[:2000] or self._transcript_seed(ctx)
        candidates = await self._build_candidates(seed)
        health = self._build_health()
        instruction = _REVIEW_INSTRUCTION.format(candidates=candidates, health=health)

        messages = list(ctx.messages) + [{"role": "user", "content": instruction}]
        log("技能后台评审开始（transcript 上下文）", "DEBUG", tag="技能")
        output = await self._mind.reflect(
            messages,
            max_iterations=_MAX_REVIEW_ITERATIONS,
            tool_tags=["skills"],
            allow_output_tools=False,
        )
        log("技能后台评审完成", "DEBUG", tag="技能")
        return (output or "").strip() or None

    @staticmethod
    def _transcript_seed(ctx: HookContext) -> str:
        """执行摘要缺失时的语义检索种子：取 transcript 末段文本。"""
        for msg in reversed(ctx.messages):
            content = msg.get("content")
            if isinstance(content, str) and content.strip():
                return content.strip()[:2000]
        return ""
