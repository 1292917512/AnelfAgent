"""判断工具 — AI 的结构化评判入口（group="judgment"）。

把"需要模型拍板的判断"从聊天生成中分离出来：AI 设计窄问题
（Choice 选择 / Score 评分 / Noul 是非），引擎经 TypeSafe 原生通道
或普通模型回退通道并行评判，返回类型化答案 + 概率分布 + 置信度。
通道与阈值的治理参数走统一配置面（judgment/core 组）。

评判上下文三档供给（context_mode）：none（默认，显式传 state）/
conversation（注入当前会话最近消息）/ full（注入窗口全量 + 折叠摘要，
慎用）。state 与非 none 模式互斥。
"""
from __future__ import annotations

import json

from agent.judgment.engine import get_judgment_engine
from agent.judgment.types import JsonValue, JudgmentError, parse_questions
from core.config import get_config_bool, get_config_int
from core.log import log
from core.tool_errors import ErrorCause, tool_error
from entities._sdk import deferred_tool

#: 注入时单条消息的字符上限（长消息截断，防单条撑满护栏）
_MESSAGE_CHAR_CAP = 500

_CONTEXT_MODES = ("none", "conversation", "full")


def _judgment_enabled() -> bool:
    return get_config_bool("judgment_enabled", True)


async def _capture_scope_context(*, full: bool) -> str:
    """注入当前会话上下文：最近消息（conversation）或窗口全量+折叠摘要（full）。

    直读 sqlite（无折叠调度/水位副作用）；非会话 scope、无历史、读取失败
    均返回空串。字符护栏从最新往前保留；full 档折叠摘要在剩余预算内前置。
    """
    from agent.messages import build_scope_id, is_conversation_scope, parse_entity_scope
    from agent.mind.tool_activation import ToolActivationManager
    from agent.mind.tools.ports import mind_port

    scope = ToolActivationManager.current_scope()
    if not is_conversation_scope(scope) or not mind_port.bound:
        return ""
    scope_type, adapter, base_id, session_id = parse_entity_scope(scope)
    suffix = f"#{session_id}" if session_id and session_id != base_id else ""
    scope_id = build_scope_id(adapter, base_id, suffix)

    max_chars = get_config_int(
        "judgment_full_max_chars" if full else "judgment_context_max_chars",
        20000 if full else 4000,
    )
    try:
        data = mind_port.get().conversation_data
        sqlite = data.router.sqlite
        limit = data.max_size if full else get_config_int("judgment_context_messages", 6)
        if limit <= 0:
            return ""
        rows = await sqlite.fetch_conversation(
            scope_type=scope_type, scope_id=scope_id, limit=limit
        )
        summary_text = ""
        if full:
            from agent.storage.scope_migrate import resolve_summary_scope
            sum_type, sum_id = await resolve_summary_scope(sqlite, scope_type, scope_id)
            summary_row = await sqlite.get_conversation_summary(
                scope_type=sum_type, scope_id=sum_id
            )
            summary_text = str((summary_row or {}).get("summary") or "").strip()
    except Exception as exc:
        log(f"判断上下文注入读取失败: {exc}", "DEBUG", tag="判断")
        return ""

    lines: list[str] = []
    total = 0
    for row in reversed(rows):  # 字符护栏从最新往前保留
        text = str(row.get("content") or "").strip()
        if not text:
            continue
        if len(text) > _MESSAGE_CHAR_CAP:
            text = text[:_MESSAGE_CHAR_CAP] + "…"
        line = f"[{row.get('role', '?')}] {text}"
        if total + len(line) > max_chars and lines:
            break
        lines.append(line)
        total += len(line)
    lines.reverse()
    if full and summary_text:
        remaining = max_chars - total
        if remaining > 200:
            lines.insert(0, f"[对话摘要] {summary_text[:remaining]}")
    return "\n".join(lines)


@deferred_tool(
    group="judgment",
    tags=["always"],
    check_fn=_judgment_enabled,
    concurrency_safe=True,
    timeout=300.0,
    # 复杂参数的完整 wire schema（签名推导只到顶层类型）
    schema_extra={
        "questions": {
            "minItems": 1,
            "items": {
                "type": "object",
                "required": ["id", "type", "instructions"],
                "properties": {
                    "id": {"type": "string", "description": "问题 ID（结果按此键返回，不发给模型）"},
                    "type": {"type": "string", "enum": ["choice", "score", "noul"]},
                    "instructions": {"description": "评判指令（字符串或结构化对象）"},
                    "criteria": {
                        "description": "choice=选项名→描述字典；score=等级描述有序数组；"
                                       "noul={\"true\",\"false\"}（可选）"
                    },
                },
            },
        },
        "context_mode": {"enum": list(_CONTEXT_MODES)},
    },
)
async def judge(questions: list, state: str = "", context_mode: str = "none") -> str:
    """结构化判断：选择(choice)/评分(score)/是非(noul) 三类问题可混合，一次调用并行评判。

    适用场景：路由分派、风险/质量评分、条件核验、候选排序——任何
    "需要拍板但不需要生成文本"的环节。每题只问一个窄判断；复杂判断
    拆成多题一次问完（并行评判，题数几乎不影响耗时）。

    Args:
        questions: 问题数组，每项 {"id", "type", "instructions", "criteria"}：
            id 自取（结果按此键返回，不发给模型）；
            type 为 choice/score/noul；
            instructions 为评判指令（字符串，需精确语义时可给结构化对象）；
            criteria 按题型：choice=选项名→描述字典（选项要互相拉开边界，
                列表可能不全覆盖时加 other 项）；score=等级描述的有序数组
                （2~10 级，每级写具体情境而非程度词）；noul 可省略，边界
                模糊时给 {"true": ..., "false": ...} 定义两侧语义
        state: 被评判的内容（纯文本，或 JSON 字符串——结构化内容的问题里
            可用 `字段路径` 引用嵌套数据）。与 context_mode 非 none 互斥
        context_mode: 评判上下文来源。none（默认）= 只评判显式传入的 state；
            conversation = 注入当前会话最近消息（评判用户意图/情绪/分派等
            对话内容的常态选择，不必把消息抄进 state）；
            full = 注入当前会话窗口全量消息 + 折叠摘要（**慎用**：输入
            token 成本高、长上下文稀释判断注意力，仅在判断确实依赖
            完整对话脉络时使用）

    返回 answers：每题含概率分布；choice/score 另有 confidence
    （分布集中度，峰值=1 均匀=0）——高置信可直接行动，低置信宜向用户
    确认或换更明确的选项描述重问。source 标记本次由 TypeSafe 原生
    还是普通模型回退通道作答；state_source 标记评判上下文来源
    （provided / auto_conversation / auto_full）。
    """
    if context_mode not in _CONTEXT_MODES:
        return tool_error(
            f"非法 context_mode: '{context_mode}'",
            cause=ErrorCause.PARAM,
            retryable=False,
            hint=f"可选值: {'/'.join(_CONTEXT_MODES)}",
        )
    try:
        parsed = parse_questions(questions)
    except JudgmentError as exc:
        return tool_error(str(exc), cause=exc.cause, retryable=exc.retryable)

    text = state.strip()
    if text and context_mode != "none":
        return tool_error(
            "state 与 context_mode 互斥：显式传入内容时 context_mode 须为 none",
            cause=ErrorCause.PARAM,
            retryable=False,
        )
    state_source = "provided"
    if not text:
        if context_mode == "none":
            return tool_error(
                "缺少评判上下文：省略 state 时须用 context_mode 选择注入档位",
                cause=ErrorCause.PARAM,
                retryable=False,
                hint="传 state（文本或 JSON 字符串），或 context_mode=conversation/full 注入当前会话上下文",
            )
        text = await _capture_scope_context(full=context_mode == "full")
        state_source = "auto_full" if context_mode == "full" else "auto_conversation"
        if not text:
            return tool_error(
                f"当前会话上下文注入为空（context_mode={context_mode}）",
                cause=ErrorCause.PARAM,
                retryable=False,
                hint="需要在会话上下文中调用；评判非对话内容请显式传 state",
            )

    parsed_state: JsonValue = text
    if text.startswith(("{", "[")):
        try:
            parsed_state = json.loads(text)
        except (json.JSONDecodeError, ValueError):
            pass

    try:
        report = await get_judgment_engine().judge(parsed_state, parsed)
    except JudgmentError as exc:
        hint = None
        if exc.cause == ErrorCause.CONFIG:
            hint = "配置 TypeSafe API Key（judgment_api_key）或开启 judgment_fallback_enabled"
        return tool_error(str(exc), cause=exc.cause, retryable=exc.retryable, hint=hint)
    return json.dumps(
        {
            "ok": True,
            "source": report.source.value,
            "model": report.model,
            "state_source": state_source,
            "answers": {
                qid: answer.model_dump() for qid, answer in report.answers.items()
            },
            "missing": report.missing,
            "usage": report.usage.model_dump(),
        },
        ensure_ascii=False,
    )
