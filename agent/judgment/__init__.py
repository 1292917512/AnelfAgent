"""判断能力 — 结构化评判（Choice/Score/Noul）的统一通道。

为 AI 与系统代码提供"可编程常识"原语：输入 state + 类型化问题，
输出带概率分布与置信度的类型化答案。通道双轨：配置 TypeSafe API Key
走 Jev 原生评判（校准分布、并行隔离）；未配置或失败时回退普通聊天
模型（同构输出，置信度同公式本地合成）。

AI 调用面：mind 核心工具组 judgment（judge 工具，tags=["always"]）；
系统调用面：``get_judgment_engine().judge(state, questions)``；
治理面：统一配置组 judgment/core（AI 经 update_entity_config 自配置）。
"""
from __future__ import annotations

from agent.judgment import configs  # noqa: F401  导入即注册统一配置面
from agent.judgment.engine import JudgmentEngine, get_judgment_engine
from agent.judgment.types import (
    Answer,
    ChoiceAnswer,
    ChoiceQuestion,
    EntryValue,
    JsonValue,
    JudgmentError,
    JudgmentReport,
    JudgmentSource,
    JudgmentUsage,
    NoulAnswer,
    NoulCriteria,
    NoulQuestion,
    Question,
    ScoreAnswer,
    ScoreQuestion,
    compute_confidence,
    parse_questions,
)
from core.entity import EntityRegistry

# 分组排序权重：0-9 输出思维段（thinking=1 之后）
EntityRegistry.register_group_order("judgment", 2)

__all__ = [
    "Answer",
    "ChoiceAnswer",
    "ChoiceQuestion",
    "EntryValue",
    "JsonValue",
    "JudgmentEngine",
    "JudgmentError",
    "JudgmentReport",
    "JudgmentSource",
    "JudgmentUsage",
    "NoulAnswer",
    "NoulCriteria",
    "NoulQuestion",
    "Question",
    "ScoreAnswer",
    "ScoreQuestion",
    "compute_confidence",
    "get_judgment_engine",
    "parse_questions",
]
