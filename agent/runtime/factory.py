"""运行时工厂辅助函数。

人謭加载逻辑，供 bootstrap.py 节点调用。
"""

from __future__ import annotations

from typing import List

from core.log import log


def load_persona():
    """从人设目录加载活跃人设。"""
    from agent.config import get_config_provider
    from agent.messages import CharacterAgent
    from core.tags import get_tag_desc

    provider = get_config_provider()
    persona_data = provider.get_persona_config()
    # 拷贝一份，避免就地修改配置提供者返回的共享 personality 列表
    prompts: List[str] = list(persona_data.get("personality", []))

    tag_prompt = f"消息中的 [key:value] 是元数据标签：{get_tag_desc()}回复正文中不要输出任何标签。"
    if tag_prompt not in prompts:
        prompts.append(tag_prompt)

    persona_name = persona_data.get("name", provider.get_active_persona_name() or "default")
    log(f"已加载人设: {persona_name} ({len(prompts)} 条提示词)")
    return CharacterAgent(personality=prompts)
