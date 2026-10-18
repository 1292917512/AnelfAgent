"""对话结束后异步评审技能沉淀，事实归系统、决策归 AI。

默认继承完整消息链及工具目录，评审指令与候选事实追加在尾部，
保留稳定前缀的缓存机会。模型切换或工具目录变化仍可能降低缓存命中。
仅依据本轮操作事实筛选评审，纯问答默认不启动模型；阈值可由 AI/Web 调整。
同会话冷却限制触发频率；单钩子并发为 1，执行队列由钩子宿主管理。
评审禁止对外发送消息，产出只用于观测，成功完成不唤醒新回复。
模型在尾部获得写入确认和收尾规则及当前长度建议；少量提示 token 换取减少重复调用。
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Optional

from agent.hooks_llm import HookContext, HookRegistry, llm_hook
from agent.judgment.evidence import conversation_evidence
from agent.judgment.gate import assess_optional_work
from agent.skills.skill_index import SkillIndex
from agent.skills.skill_store import SkillStore
from core.config import get_config_int
from core.log import log

if TYPE_CHECKING:
    from agent.mind.mind import Mind

_HOOK_NAME = "skill_review"
_HOOK_OWNER = "skills.review"

_REVIEW_INSTRUCTION = """{context_description}

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
2. 相近技能可容纳本次经验 → **合并优先**：基于旧正文整理，用 update_skill 提交合并后的完整正文
   （删除过时部分，不要只增不减），或对库现状中的合并候选用 merge_skills 显式合并
3. 确有差异需新建 → create_skill
4. 库现状中的问题技能（零参与/高匹配零消费/触发词互相竞争/高频改写未收敛/
   解析失败）可顺手治理：merge / update 收敛 / create_skill 同名重建修复 / 不管（有理由地保留）

## 写作与召回
- 正文：步骤 + 坑 + 注意事项，markdown 列表化，建议 {body_advise_chars} 字内，保留必要细节
- **禁止**写：结果性内容（"帮某人查到了 X"）、流水账、AI 自述
- 触发词：3~8 个高区分度词，宁少勿泛；只在真实漏召回后补充，不做预防性堆砌
  （工具会报告跨技能碰撞的泛词，此时换更具体的表述或合并竞争技能）
- create_skill / update_skill 返回 needs_decision 表示尚未写入，不是接口故障；
  阅读 facts 后调整内容，或带 decision（取舍理由）确认同一份内容，或放弃，不原样无 decision 重试
- 成功回执中的 advisory 只是建议，内容已经保存；不要为消除提示再次写入
- 本轮最多一次成功写入（create / update / merge 三选一）；成功后 end_reply 收尾
- 本轮模型调用预算 {iteration_budget}，提前预留确认与收尾轮次；预算不足时说明未完成项，不反复试写

## 工作原则
- 多数轮次都是无价值的，先看相近候选再下结论，不一拍脑门写新技能
- 技能库是有限容量的资产：每次写入都在花库容，合并与删除同样是贡献
- **治理操作必须走技能工具**（create_skill / update_skill / merge_skills），
  禁止直接用文件工具编辑 SKILL.md——手写 frontmatter 会破坏格式契约
"""

_MAX_CANDIDATES = 10
# 评审触发最小材料：transcript 快照至少包含非空消息链才评审
_MIN_TRANSCRIPT_MESSAGES = 2
# 同会话评审最小触发间隔，不限制单次执行时长
_DEFAULT_COOLDOWN_SECONDS = 120.0

_CONTROL_TOOLS = frozenset({"end_reply", "send_message", "send_file", "present_plan", "update_goal",
                            "check_background_tasks", "list_entity_methods", "activate_tool_group",
                            "deactivate_tool_group"})


def needs_review(payload: dict[str, Any]) -> bool:
    """按本轮操作量、失败证据和技能使用筛选材料，不判断经验是否值得写入。"""
    messages = payload.get("messages")
    if payload.get("error") or not isinstance(messages, list) or len(messages) < _MIN_TRANSCRIPT_MESSAGES:
        return False
    minimum = max(0, get_config_int("skills_review_min_operations", 3))
    operations = [item for item in payload.get("executed_tools", [])
                  if isinstance(item, dict) and item.get("name") not in _CONTROL_TOOLS]
    return (len(operations) >= minimum or any(item.get("success") is False for item in operations)
            or any(item.get("name") == "get_skill" for item in operations))


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
        self._register_hook()
        self._started = True
        log("技能后台评审已启动（经 LLM 钩子面）", "DEBUG", tag="技能")

    def stop(self) -> None:
        """停止评审（注销钩子）。"""
        HookRegistry.unregister(_HOOK_NAME)
        self._started = False

    def _register_hook(self) -> None:
        """注册 skill_review 钩子（transcript 档位 + 条件门控）。"""
        reviewer = self

        @llm_hook(
            _HOOK_NAME, event="after_reply", context="transcript",
            when=needs_review, tool_tags=["skills"], allow_output_tools=False,
            route_output=False,
            max_iterations=6, max_concurrent=1,
            cooldown_seconds=_DEFAULT_COOLDOWN_SECONDS,
            owner=_HOOK_OWNER, source="code",
            config_key="skills_review_enabled",
            description="按本轮操作证据评审执行过程，自主决策技能沉淀/合并/治理",
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
        执行参数（工具面/轮次预算）透传钩子声明，不在执行体另立常量。
        返回产出文本供钩子面登记（None/空 = 无沉淀）。
        """
        health = self._build_health()
        decision = await assess_optional_work(
            "技能评审",
            "是否值得启动完整技能评审？新方法、排障经验、失败后修正、现有技能修订或技能库治理问题均应继续；"
            "纯例行成功操作且没有可复用经验或治理线索时可跳过。这里只决定是否评审，不决定写入技能。",
            {"scope": ctx.scope, "transcript_available": ctx.transcript_available(),
             "conversation": conversation_evidence(ctx.messages) if ctx.transcript_available() else [],
             "execution_summary": str(ctx.payload.get("execution_summary") or "")[:4000] if ctx.transcript_available() else "",
             "library_health": health},
        )
        if not decision.run:
            return decision.describe() + "：本轮无需技能评审"
        if ctx.transcript_available():
            context_description = "以上是本轮对话的完整执行上下文（你的回复、工具调用与结果原文）。"
            execution_summary = str(ctx.payload.get("execution_summary") or "")
            seed = execution_summary[:2000] or self._transcript_seed(ctx)
        else:
            context_description = "本次未继承原对话执行过程。仅按下面的技能库事实检查重复与失效内容；不得推测原对话或据此沉淀新经验。"
            seed = ""
        candidates = await self._build_candidates(seed)
        instruction = _REVIEW_INSTRUCTION.format(
            context_description=context_description, candidates=candidates, health=health,
            body_advise_chars=get_config_int("skills_body_advise_chars", 2000),
            iteration_budget=str(ctx.max_iterations) if ctx.max_iterations else "沿用主思维配置，以轮内执行状态为准",
        )

        messages = list(ctx.messages) + [{"role": "user", "content": instruction}]
        log(f"技能后台评审开始（{ctx.context_mode.value} 上下文）", "DEBUG", tag="技能")
        output = await self._mind.reflect(
            messages,
            max_iterations=ctx.max_iterations,
            tool_tags=list(ctx.tool_tags),
            allow_output_tools=ctx.allow_output_tools,
            options={"_model_id": ctx.model} if ctx.model else None,
            inherited_tools=ctx.inherited_tools,
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
