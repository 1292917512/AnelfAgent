"""judgment 配置注册（统一配置面，AI 经 get/update_entity_config 热调）。"""
from __future__ import annotations

from core.config import register_configs_safe

_JUDGMENT_CONFIGS = {
    "judgment/core": {
        "judgment_enabled": {
            "description": "是否启用判断能力（关闭后 judge 工具从目录隐藏，引擎调用直接拒绝）",
            "default": True,
        },
        "judgment_timeout": {
            "description": "单次判断的总等待预算，原生候选与普通模型回退共用，不按模型数量叠加",
            "default": 30.0,
            "value_type": "range",
            "min": 5.0,
            "max": 120.0,
            "unit": "秒",
        },
        "judgment_fallback_enabled": {
            "description": "未配置判断服务或原生通道失败时，是否回退到普通聊天模型完成判断",
            "default": True,
        },
        "judgment_gate_enabled": {
            "description": "用原生 Jev 预判自动深探与技能评审是否必要；未配置、失败或不确定时照常执行，不调用普通模型回退",
            "default": True,
        },
        "judgment_gate_timeout": {
            "description": "自动回复审核与可选工作预判的等待预算；到期正文按投递开关正常发送，可选工作保留原流程",
            "default": 5.0, "min": 0.1, "max": 30.0, "unit": "秒", "advanced": True,
        },
        "judgment_evidence_max_chars": {
            "description": "自动判断的会话证据共享预算；优先保留当前请求、归属标签和执行事实，不注入完整对话",
            "default": 3000, "min": 600, "max": 12000, "unit": "字符", "advanced": True,
        },
        "judgment_auto_max_chars": {
            "description": "自动审核与可选工作预判的事实总预算；超限不调用模型，正文按投递开关正常发送，可选工作照常执行。不限制显式 judge 或 SDK 输入",
            "default": 8000, "min": 2000, "max": 32000, "unit": "字符", "advanced": True,
        },
        "judgment_connect_timeout": {
            "description": "判断服务单次连接及 HEAD 检查的等待上限；连通检查按全局路由为直连和代理分别预留时间，正常服务不周期检查，不消耗模型额度",
            "default": 2.0, "min": 0.1, "max": 10.0, "unit": "秒", "advanced": True,
        },
        "judgment_recheck_seconds": {
            "description": "不可达判断模型的后台连通复查间隔；仅 HEAD，不调用模型。恢复后重按优先级选用，凭据错误须修正配置",
            "default": 300.0, "min": 30.0, "max": 3600.0, "unit": "秒",
        },
        "judgment_delivery_confidence": {
            "description": "自动回复拦截置信阈值；只有不适合投递与内部/重复/错目标类别均达到此置信度才拦截，不可用或不确定按投递开关正常发送",
            "default": 0.8, "min": 0.5, "max": 1.0, "advanced": True,
        },
        "judgment_gate_skip_threshold": {
            "description": "必要概率不高于此值，且无需执行的依据概率不低于 1−此值，才跳过可选工作",
            "default": 0.1, "min": 0.0, "max": 0.49, "advanced": True,
        },
        "judgment_slow_seconds": {
            "description": "Jev 响应偏慢警告阈值；超过此值或预判等待预算的八成即提示，计入网络耗时",
            "default": 1.0, "min": 0.1, "max": 30.0, "unit": "秒", "advanced": True,
        },
        "judgment_fallback_model": {
            "description": "回退判断专用模型 ID（留空走默认聊天模型链；判断是轻量任务，建议小快模型）",
            "default": "",
        },
        "judgment_fallback_effort": {
            "description": "回退判断的思考档位（模型不支持思考时自动忽略）",
            "default": "low",
            "advanced": True,
        },
        "judgment_context_messages": {
            "description": "judge context_mode=conversation 时注入的当前会话最近消息条数",
            "default": 6,
            "value_type": "range",
            "min": 1,
            "max": 50,
            "unit": "条",
        },
        "judgment_context_max_chars": {
            "description": "conversation 档注入上下文的字符护栏（超出从最早的消息截断，保留最新）",
            "default": 4000,
            "advanced": True,
            "unit": "字符",
        },
        "judgment_full_max_chars": {
            "description": "full 档注入上下文的字符护栏（窗口全量消息 + 折叠摘要共用）",
            "default": 20000,
            "advanced": True,
            "unit": "字符",
        },
    },
}

register_configs_safe(_JUDGMENT_CONFIGS)
