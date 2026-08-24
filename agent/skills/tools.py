"""技能工具 — AI 可调用的技能管理接口（决策协议面）。

设计哲学：系统呈现事实，AI 持有决策权。create/update 在事实层检测到显著
信号（语义相近技能/触发词碰撞/容量水位/无实质变化）时不拒绝写入，而是返回
"需要决策"的诊断报告，AI 阅读事实后带 decision 回执重呼即完成写入，
或改走合并/放弃路径——把判断留给 AI，把事实算清楚留给系统。

SkillStore / SkillMatcher 引用经 ``skill_tools_port`` 晚绑定端口分发
（工具 import 时注册、拿不到构造参数；由 agent.runtime.wiring 统一施绑）。
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, NamedTuple, Optional, Tuple

from agent.skills import sources as skill_sources
from agent.skills.skill_matcher import SkillMatcher
from agent.skills.skill_store import SkillStore
from core.latebind import LateBinding
from core.log import log
from core.tool_errors import ErrorCause, tool_error
from entities._sdk import deferred_tool


class SkillToolDeps(NamedTuple):
    """技能工具组运行时依赖（wiring 一次性施绑）。"""

    store: SkillStore
    matcher: SkillMatcher


#: 技能工具组依赖端口（bootstrap 经 agent.runtime.wiring 施绑）
skill_tools_port: LateBinding[SkillToolDeps] = LateBinding("skills.tools")


def _deps() -> Optional[SkillToolDeps]:
    """取技能工具依赖（端口未施绑时返回 None，工具降级为未就绪错误）。"""
    return skill_tools_port.get() if skill_tools_port.bound else None


def _not_ready() -> str:
    return tool_error(
        "技能系统未初始化",
        cause=ErrorCause.STATE, retryable=False,
        hint="技能组件未初始化，请检查服务启动状态",
    )


def _index() -> Any:
    """事实索引（matcher 持有；未初始化时返回 None，工具降级为直通写入）。"""
    deps = _deps()
    return deps.matcher.index if deps else None


def _decision_request(facts: Dict[str, Any], guidance: str) -> str:
    """构造决策请求响应：呈现事实 + 决策路径，不做任何拒绝。"""
    return json.dumps({
        "status": "needs_decision",
        "facts": facts,
        "guidance": guidance,
        "options": [
            {"action": "merge", "how": "差异点并入相近技能：update_skill 增量补充，或 merge_skills 显式合并（源可逆归档）"},
            {"action": "confirm", "how": "确有差异需独立成篇：重呼本工具并带 decision 参数写明差异理由"},
            {"action": "abort", "how": "事实改变了判断：直接放弃本次写入"},
        ],
    }, ensure_ascii=False)


async def _advisory_or_none(
        *, name: str, description: str, content: str,
        patterns: List[str], updating: bool,
) -> Optional[Dict[str, Any]]:
    """计算写入诊断；无 matcher/index 或无显著事实时返回 None（快路径直通）。"""
    index = _index()
    if index is None:
        return None
    try:
        facts = await index.write_advisory(
            name=name, description=description, content=content,
            trigger_patterns=patterns, updating=updating,
        )
        return facts or None
    except Exception as exc:
        log(f"技能写入诊断失败（降级直通）: {exc}", "DEBUG", tag="技能")
        return None


@deferred_tool(
    group="skills", tags=["always"], source="mind.skills", timeout=90.0,
    description="创建一个新技能：将可复用的方法、流程或知识保存下来，供以后遇到相似任务时参考。"
                "当库中存在相近技能等显著事实时，首次调用会返回诊断报告（不写入），"
                "阅读后带 decision 参数（差异理由）重呼即完成创建，或改走合并路径。",
)
async def create_skill(
        name: str, description: str, content: str,
        trigger_patterns: str = "", decision: str = "",
) -> str:
    """创建新技能。

    Args:
        name: 技能名（英文短横线命名，如 web-research）
        description: 一句话描述技能用途（场景 + 目的）
        content: 技能内容（markdown，步骤/要点/注意事项）
        trigger_patterns: 触发关键词，逗号分隔（3~8 个高区分度词，宁少勿泛）
        decision: 决策回执：首次调用返回诊断报告后，写明差异理由重呼以确认写入
    """
    deps = _deps()
    if deps is None:
        return _not_ready()
    patterns = [p.strip() for p in trigger_patterns.split(",") if p.strip()]
    if not decision.strip():
        facts = await _advisory_or_none(
            name=name, description=description, content=content,
            patterns=patterns, updating=False,
        )
        if facts is not None:
            return _decision_request(
                facts,
                "本次拟议创建与技能库现状存在上述显著事实，请决策：合并 / 确认新建 / 放弃。",
            )
    skill = deps.store.create(
        name=name, description=description, content=content,
        trigger_patterns=patterns, created_by="agent",
        rationale=decision.strip(),
    )
    return json.dumps({
        "ok": True, "name": skill.name,
        "message": f"技能 '{skill.name}' 已创建",
    }, ensure_ascii=False)


@deferred_tool(
    group="skills", tags=["always"], source="mind.skills", timeout=90.0,
    description="更新已有技能：改进内容、补充触发词。增量更新，会记录 patch 次数。"
                "当检测到无实质变化/触发词碰撞等显著事实时，首次调用返回诊断报告（不写入），"
                "阅读后带 decision 参数（更新理由）重呼即完成更新。",
)
async def update_skill(
        name: str, content: str = "", description: str = "",
        add_trigger_patterns: str = "", decision: str = "",
) -> str:
    """更新技能。

    Args:
        name: 要更新的技能名
        content: 新的技能内容（完整替换旧内容，留空则不更新；合并时删除过时部分，不要只增不减）
        description: 新的描述（留空则不更新）
        add_trigger_patterns: 追加的触发关键词，逗号分隔（仅在真实漏召回后补充，不做预防性堆砌）
        decision: 决策回执：首次调用返回诊断报告后，写明更新理由重呼以确认写入
    """
    deps = _deps()
    if deps is None:
        return _not_ready()
    patterns = [p.strip() for p in add_trigger_patterns.split(",") if p.strip()]
    if not decision.strip():
        facts = await _advisory_or_none(
            name=name, description=description, content=content,
            patterns=patterns, updating=True,
        )
        if facts is not None:
            if "not_found" in facts:
                return tool_error(
                    f"技能 '{name}' 不存在", cause=ErrorCause.NOT_FOUND, retryable=False,
                )
            return _decision_request(
                facts,
                "本次拟议更新存在上述显著事实，请决策：调整后重试 / 确认更新 / 放弃。",
            )
    skill = deps.store.patch(
        name,
        content=content or None,
        description=description or None,
        add_trigger_patterns=patterns or None,
        rationale=decision.strip(),
    )
    if skill is None:
        return tool_error(f"技能 '{name}' 不存在", cause=ErrorCause.NOT_FOUND, retryable=False)
    return json.dumps({
        "ok": True, "name": skill.name, "patch_count": skill.patch_count,
        "message": f"技能 '{skill.name}' 已更新（第 {skill.patch_count} 次修订）",
    }, ensure_ascii=False)


@deferred_tool(
    group="skills", tags=["always"], source="mind.skills",
    description="合并技能：将若干冗余技能合并进一个目标技能（目标更新为合并后内容，源可逆归档）。"
                "用于治理技能库中的近重复技能（诊断报告/库健康中的合并候选）。",
)
def merge_skills(
        sources: str, target: str, content: str,
        description: str = "", trigger_patterns: str = "",
) -> str:
    """合并技能。

    Args:
        sources: 要合并的源技能名，逗号分隔（将被归档，记录 merged_into 可恢复）
        target: 合并目标技能名（必须已存在，接收合并后内容）
        content: 合并后的完整内容（去重收敛后的版本，删除各源中过时的部分）
        description: 合并后的新描述（留空则保留目标原描述）
        trigger_patterns: 合并后的补充触发关键词，逗号分隔
    """
    deps = _deps()
    if deps is None:
        return _not_ready()
    source_list = [p.strip() for p in sources.split(",") if p.strip()]
    if not source_list:
        return tool_error(
            "sources 不能为空", cause=ErrorCause.PARAM, retryable=False,
            hint="提供至少一个要合并的源技能名",
        )
    patterns = [p.strip() for p in trigger_patterns.split(",") if p.strip()]
    merged = deps.store.merge(
        source_list, target,
        content=content, description=description,
        add_trigger_patterns=patterns or None,
    )
    if merged is None:
        return tool_error(
            f"合并目标 '{target}' 不存在", cause=ErrorCause.NOT_FOUND, retryable=False,
        )
    return json.dumps({
        "ok": True, "target": merged.name,
        "archived_sources": [s.strip() for s in source_list if s.strip() != merged.name],
        "message": f"已合并进 '{merged.name}'，源技能已归档（merged_into 可追溯恢复）",
    }, ensure_ascii=False)


@deferred_tool(
    group="skills", tags=["always"], source="mind.skills", timeout=120.0,
    description="技能库健康报告：库容、零参与技能、高匹配零消费、触发词碰撞、合并信号、相似聚类。"
                "治理技能库（合并/归档/精简触发词）前先看此报告。",
)
async def skill_library_health() -> str:
    """查看技能库健康报告。"""
    index = _index()
    if index is None:
        deps = _deps()
        if deps is None:
            return _not_ready()
        from agent.skills.skill_index import SkillIndex
        index = SkillIndex(deps.store)
    payload: Dict[str, Any] = dict(index.snapshot())
    try:
        await index.warm(limit=32)
        clusters = await index.clusters()
        if clusters:
            payload["similar_clusters"] = [
                [{"name": s.name, "use_count": s.use_count} for s in cluster]
                for cluster in clusters[:10]
            ]
    except Exception as exc:
        log(f"技能聚类计算失败: {exc}", "DEBUG", tag="技能")
    return json.dumps(payload, ensure_ascii=False)


@deferred_tool(
    group="skills", tags=["always"], source="mind.skills",
    description="搜索技能：默认搜本地技能库（关键词+语义匹配）；scope='external' 搜外部技能商店"
                "（如 SkillHub，本地无匹配或用户想找现成技能时用），scope='all' 两者都搜。",
)
async def search_skills(query: str, top_k: int = 5, scope: str = "local", category: str = "") -> str:
    """搜索技能。

    Args:
        query: 搜索关键词或描述（外部商店为分词搜索，建议用同义/上位词多次搜索后合并筛选）
        top_k: 每类来源最多返回数量，默认 5
        scope: 搜索范围：local（本地技能库，默认）/ external（外部技能商店）/ all（两者）
        category: 外部商店的一级分类过滤（留空不过滤；可选值见 list_skill_sources 返回）
    """
    deps = _deps()
    if deps is None:
        return _not_ready()
    scope = (scope or "local").strip().lower()
    if scope not in ("local", "external", "all"):
        return tool_error(
            f"无效的 scope: {scope}", cause=ErrorCause.PARAM, retryable=False,
            hint="可选值：local / external / all",
        )
    limit = max(1, min(top_k, 20))
    payload: Dict[str, Any] = {"ok": True, "scope": scope, "query": query}
    if scope in ("local", "all"):
        matched = await deps.matcher.match([query], top_k=limit, min_score=0.0)
        payload["local"] = [
            {
                "name": skill.name,
                "description": skill.description,
                "trigger_patterns": skill.trigger_patterns,
                "use_count": skill.use_count,
                "score": round(score, 3),
            }
            for skill, score in matched
        ]
        if not matched:
            payload["local_hint"] = (
                "本地无匹配技能：可用 scope='external' 搜索外部技能商店，"
                "或在任务完成后用 create_skill 沉淀经验"
            )
    if scope in ("external", "all"):
        external, external_hint = await _search_external(query, limit, category)
        payload["external"] = external
        if external_hint:
            payload["external_hint"] = external_hint
    return json.dumps(payload, ensure_ascii=False)


async def _search_external(query: str, limit: int, category: str) -> Tuple[List[Dict[str, Any]], str]:
    """聚合全部可用外部技能源的搜索结果（按源 + slug 去重）。"""
    sources = [s for s in skill_sources.list_sources() if s.is_available()]
    if not sources:
        return [], "未接入可用的外部技能源（agent/skills/sources/ 下无可用模块）"
    merged: Dict[Tuple[str, str], Dict[str, Any]] = {}
    errors: List[str] = []
    for source in sources:
        try:
            results = await source.search(query, category=category, top_k=limit)
        except ValueError as exc:
            errors.append(str(exc))
            continue
        except Exception as exc:
            log(f"外部技能源搜索失败 ({source.key}): {exc}", "WARNING", tag="技能")
            errors.append(f"{source.display_name} 搜索失败: {exc}")
            continue
        for item in results:
            merged[(source.key, item.slug)] = item.to_dict()
    if not merged:
        if errors:
            return [], "；".join(errors)
        return [], "外部技能商店无匹配结果，可去掉 category 或换同义/上位词重试"
    hint = "安装外部技能前请先向用户说明并确认，再用 install_external_skill 安装"
    if errors:
        hint += f"（部分源搜索失败：{'；'.join(errors)}）"
    return list(merged.values()), hint


@deferred_tool(
    group="skills", tags=["always"], source="mind.skills",
    description="列出全部技能（名称、描述、使用/匹配次数、状态）。",
)
def list_skills(include_archived: bool = False) -> str:
    """列出技能。

    Args:
        include_archived: 是否包含已归档的技能，默认否
    """
    deps = _deps()
    if deps is None:
        return _not_ready()
    skills = deps.store.list_skills(include_archived=include_archived)
    results = [
        {
            "name": s.name,
            "description": s.description,
            "state": s.state.value,
            "use_count": s.use_count,
            "match_count": s.match_count,
            "patch_count": s.patch_count,
            "pinned": s.pinned,
        }
        for s in skills
    ]
    return json.dumps({"ok": True, "count": len(results), "skills": results}, ensure_ascii=False)


@deferred_tool(
    group="skills", tags=["always"], source="mind.skills",
    description="列出已接入的外部技能源（可插拔模块，如 SkillHub）及其可用状态与支持分类。",
)
def list_skill_sources() -> str:
    """列出外部技能源。"""
    sources = skill_sources.list_sources()
    results = [
        {
            "key": s.key,
            "name": s.display_name,
            "description": s.description,
            "available": s.is_available(),
            "categories": list(s.categories),
        }
        for s in sources
    ]
    payload: Dict[str, Any] = {"ok": True, "count": len(results), "sources": results}
    if not results:
        payload["hint"] = "未接入外部技能源（agent/skills/sources/ 下无可用模块），仅本地技能库可用"
    return json.dumps(payload, ensure_ascii=False)


@deferred_tool(
    group="skills", tags=["always"], source="mind.skills",
    description="从外部技能源安装技能到本地技能库（workspace/skills/），安装后无需重启即可被"
                "搜索与自动匹配。安装前应先向用户确认。",
)
def install_external_skill(slug: str, namespace: str = "", source: str = "") -> str:
    """安装外部技能源中的技能。

    Args:
        slug: 技能 slug（search_skills scope='external' 结果中的 slug 字段）
        namespace: 命名空间（结果中的 namespace 字段，无则留空）
        source: 技能源 key（list_skill_sources 查看；只有一个可用源时可留空自动选择）
    """
    deps = _deps()
    if deps is None:
        return _not_ready()
    sources = {s.key: s for s in skill_sources.list_sources() if s.is_available()}
    if not sources:
        return tool_error(
            "未接入可用的外部技能源", cause=ErrorCause.STATE, retryable=False,
            hint="外部技能源为可插拔模块，位于 agent/skills/sources/",
        )
    if source:
        chosen = sources.get(source)
        if chosen is None:
            return tool_error(
                f"未知技能源: {source}", cause=ErrorCause.PARAM, retryable=False,
                hint=f"可用技能源: {', '.join(sources)}",
            )
    elif len(sources) == 1:
        chosen = next(iter(sources.values()))
    else:
        return tool_error(
            "存在多个技能源，请用 source 参数指定", cause=ErrorCause.PARAM, retryable=False,
            hint=f"可用技能源: {', '.join(sources)}",
        )
    if deps.store.exists(slug):
        return tool_error(
            f"技能 '{slug}' 已存在", cause=ErrorCause.STATE, retryable=False,
            hint="如需更新可先删除旧技能再安装，或用 update_skill 修改现有技能",
        )
    result = chosen.install(slug=slug, namespace=namespace, skills_dir=deps.store.skills_dir)
    if not result.ok:
        return tool_error(
            f"安装失败: {result.error}", cause=ErrorCause.INTERNAL, retryable=True,
            hint=result.hint or chosen.install_hint(),
        )
    # 触发一次读取校验（同时让外部变更感知立即生效）
    skill = deps.store.get(slug)
    return json.dumps({
        "ok": True, "name": slug, "source": chosen.key, "path": result.path,
        "loaded": skill is not None,
        "message": f"技能 '{slug}' 已从 {chosen.display_name} 安装，可被 search_skills 检索与自动匹配",
    }, ensure_ascii=False)


@deferred_tool(
    group="skills", tags=["always"], source="mind.skills",
    description="查看某个技能的完整内容。",
)
def get_skill(name: str) -> str:
    """查看技能详情。

    Args:
        name: 技能名
    """
    deps = _deps()
    if deps is None:
        return _not_ready()
    skill = deps.store.get(name)
    if skill is None:
        return tool_error(f"技能 '{name}' 不存在", cause=ErrorCause.NOT_FOUND, retryable=False)
    # 读全文计一次使用但不刷新活动时间（评审查阅候选是检查，不是消费）；
    # 先计数再重读，返回真实值而非手工补偿
    deps.store.record_use(name, touch=False)
    skill = deps.store.get(name) or skill
    return json.dumps({
        "ok": True,
        "name": skill.name,
        "description": skill.description,
        "trigger_patterns": skill.trigger_patterns,
        "content": skill.content,
        "state": skill.state.value,
        "use_count": skill.use_count,
        "match_count": skill.match_count,
        "patch_count": skill.patch_count,
        "rationale": skill.rationale,
        "merged_into": skill.merged_into,
    }, ensure_ascii=False)
