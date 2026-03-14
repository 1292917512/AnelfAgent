"""运行时工厂辅助函数。

人謭加载逻辑，供 bootstrap.py 节点调用。
"""

from __future__ import annotations

import traceback as _tb
from typing import List

from core.log import log


def load_persona():
    """从人设目录加载活跃人设。"""
    from agent.core.messages import CharacterAgent
    from core.tags import get_tag_desc
    from agent.core.config import get_config_provider

    provider = get_config_provider()
    persona_data = provider.get_persona_config()
    prompts: List[str] = persona_data.get("personality", [])

    tag_prompt = f"[]里面的是标签内容。{get_tag_desc()}输出的对话中不要包含任何标签。"
    if tag_prompt not in prompts:
        prompts.append(tag_prompt)

    try:
        from agent.ext.plugin_base import get_all_prompts
        for pp in get_all_prompts():
            if pp not in prompts:
                prompts.append(pp)
    except Exception:
        log(f"插件提示词注入失败\n{_tb.format_exc()}", "WARNING")

    persona_name = persona_data.get("name", provider.get_active_persona_name() or "default")
    log(f"已加载人设: {persona_name} ({len(prompts)} 条提示词)")
    return CharacterAgent(personality=prompts)
