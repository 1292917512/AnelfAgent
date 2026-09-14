"""积极性频率（proactivity_level）——AI 主动行为强度的顶层调节旋钮。

AI 可自己调节的行为配置：用户说"别这么烦"/"主动点"，AI 经
update_entity_config 调整 proactivity_level 即生效（热读取，无需重启）；
AI 也可依据互动反馈自主微调。

消费点：
- 元决策 prompt（autonomous.build_meta_decision_messages）：按档位注入
  主动行为指导（影响 proactive/reflect 等决策的触发倾向）；
- 心跳 idle 调度（heartbeat.engine._evaluate_idle_schedule）：空闲反思的
  触发拍数按积极性缩放（积极性高 → 更快进入反思/自由活动，低 → 更安静）。
"""

from __future__ import annotations

from core.config import ConfigValueType, get_config_float, register_configs_safe

CONFIG_KEY = "proactivity_level"

_CONFIGS = {
    "mind": {
        CONFIG_KEY: {
            "description": (
                "AI 主动行为积极性（0.0-1.0，默认 0.5）：越高越主动地搭话/反思/"
                "推进目标，越低越安静克制。AI 可依据用户反馈经 update_entity_config 自调"
            ),
            "default": 0.5,
            "value_type": ConfigValueType.RANGE,
            "min": 0.0,
            "max": 1.0,
            "step": 0.1,
        },
    },
}

register_configs_safe(_CONFIGS)


def get_proactivity() -> float:
    """当前积极性（热读取，钳制 0-1）。"""
    return max(0.0, min(1.0, get_config_float(CONFIG_KEY, 0.5)))


def idle_beats_factor() -> float:
    """idle 调度拍数的缩放因子：1.5 - level（0→1.5 更安静，1→0.5 更活跃）。"""
    return 1.5 - get_proactivity()


def proactivity_guidance() -> str:
    """元决策用的主动性指导文案（按当前档位 + 自调通道说明）。"""
    level = get_proactivity()
    if level <= 0.3:
        tendency = "当前偏安静：proactive/reflect 需有充分理由才触发，宁缺毋滥"
    elif level >= 0.7:
        tendency = "当前偏活跃：有合理话题即可 proactive，空闲时积极反思与推进目标"
    else:
        tendency = "当前均衡：按常态标准判断 proactive/reflect"
    return (
        f"[积极性 {level:.1f}] {tendency}。"
        f"用户若反馈太吵/太安静，经 update_entity_config 调整 {CONFIG_KEY}（0.0-1.0）"
    )
