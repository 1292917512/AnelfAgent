"""音频核心能力 — 提供者接口、统一存储、声纹身份、解析入库、AI 工具与上下文注入。

划分纪律（核心由 Agent 集成，业务由实体组件承担）：
- providers.py：ASR 转写 / 声纹提取的接口与注册表——具体实现
  （FunASR 等）以组件形式从实体经 entities._sdk.register_audio_provider 注册；
- store.py：全部音频解析产物（转写片段/声纹身份/样本池/录制单元）的统一
  内部存储，独立于任何实体业务；声纹身份可绑定实体画像 scope 双向关联；
- matcher.py / consolidate.py：声纹匹配引擎与离线整理；
- ingest.py / service.py：解析调度（优先级链回退）与入库管线统一入口；
- listen.py：源音源回听订正（源文件经 source_fetch 组件链取回）；
- tools.py / context.py：AI 工具组（deferred，bootstrap 激活）与上下文注入；
- worker.py：转写文本向量后台回填（记忆子系统 EmbeddingWorker 消化）。
"""

from agent.audio.providers import (
    KIND_ASR,
    KIND_VOICEPRINT,
    AudioAsrProvider,
    AudioProviderRegistry,
    AudioVoiceprintProvider,
    get_audio_registry,
)
from agent.audio.schemas import IngestPayload, IngestResult, SegmentIn
from agent.audio.service import AudioNotConfigured, AudioService, get_audio_service
from agent.audio.store import AudioStore, get_audio_store
from core.config import register_configs_safe

__all__ = [
    "KIND_ASR",
    "KIND_VOICEPRINT",
    "AudioAsrProvider",
    "AudioNotConfigured",
    "AudioProviderRegistry",
    "AudioService",
    "AudioStore",
    "AudioVoiceprintProvider",
    "IngestPayload",
    "IngestResult",
    "SegmentIn",
    "get_audio_registry",
    "get_audio_service",
    "get_audio_store",
]

# 核心配置项：分组名 audio，配置中心与音频页签配置 tab 自动可见
register_configs_safe({
    "audio": {
        "audio_ai_enabled": {
            "description": "是否允许 AI 调用音频库工具（说话人管理/识别/检索/编辑）",
            "default": True,
        },
        "audio_context_inject": {
            "description": "是否向 AI 上下文注入音频库摘要（说话人名单/待确认/未读）",
            "default": True,
        },
        "audio_match_threshold": {
            "description": "声纹匹配阈值（相似度 ≥ 阈值判为已知人）",
            "default": 0.75,
            "advanced": True,
            "value_type": "range",
            "min": 0,
            "max": 1,
            "step": 0.05,
        },
        "audio_merge_threshold": {
            "description": "合并阈值：离线整理时质心相似度 ≥ 此值的临时说话人"
                           "建议合并（比匹配阈值宽松）",
            "default": 0.70,
            "advanced": True,
            "value_type": "range",
            "min": 0,
            "max": 1,
            "step": 0.05,
        },
        "audio_insignificant_max_matches": {
            "description": "低价值判定：命中次数 ≤ 此值（配合时长条件；"
                           "智能合并时可一并清理）",
            "default": 2,
            "advanced": True,
            "unit": "次",
        },
        "audio_insignificant_max_audio_ms": {
            "description": "低价值判定：累计音频时长上限"
                           "（与命中次数同时满足才算低价值）",
            "default": 5000,
            "advanced": True,
            "unit": "毫秒",
        },
        "audio_max_samples_per_speaker": {
            "description": "每说话人声纹样本池上限（超出时按淘汰策略清理）",
            "default": 5,
            "advanced": True,
            "unit": "条",
        },
        "audio_sample_evict_strategy": {
            "description": "样本淘汰策略：outlier=淘汰与质心最不相似的样本"
                           "（噪音新样本也会被拒入）；fifo=淘汰最早样本",
            "default": "outlier",
        },
        "audio_centroid_match": {
            "description": "是否启用质心匹配（得分取 max(最佳样本, 均值向量)，"
                           "抑制单样本噪音提升稳定性）",
            "default": True,
        },
        "audio_auto_accumulate": {
            "description": "是否在命中已知人时自动累积新声纹样本入其样本池",
            "default": True,
        },
        "audio_auto_create_unknown": {
            "description": "是否自动为未匹配人声创建临时说话人（待确认）",
            "default": True,
        },
        "audio_skip_noise_segments": {
            "description": "是否跳过纯标点/空白语音段（不建档不计片段，防噪音建档）",
            "default": True,
        },
        "audio_min_segment_ms": {
            "description": "参与声纹识别的最小段时长（短于此只转写留存、不匹配不建档，0=不限制）",
            "default": 2000,
            "advanced": True,
            "unit": "毫秒",
        },
        "audio_attach_unidentified": {
            "description": "是否将未识别段挂到同录制最近的已归属段"
                           "（关闭则未识别段标记为未知）",
            "default": True,
        },
        "audio_ffmpeg_bin": {
            "description": "ffmpeg 可执行文件路径（非 16k 单声道 WAV 自动转码预处理）",
            "default": "ffmpeg",
        },
        "sound_provider_priority": {
            "description": "声音能力提供者优先级链（JSON 字典 {能力: [提供者]}，能力: tts/voice_mgmt/music；留空自动）",
            "default": {},
            "value_type": "json",
            "advanced": True,
        },
        "sound_voice_default": {
            "description": "默认音色预设 ID（声音页·音色面板或 voice_preset 工具管理；"
                           "空=未指派，合成入口用提供者协议音色）",
            "default": "",
        },
        "sound_voice_realtime": {
            "description": "通话音色预设 ID（空=跟随默认预设；克隆型预设不适用流式通话）",
            "default": "",
        },
        "audio_models_asr_priority": {
            "description": "内部模型 ASR 提供者在转写链中的优先级（小值优先；本地组件默认 10）",
            "default": 50,
            "advanced": True,
        },
        "funasr_timeout": {
            "description": "FunASR 服务调用超时",
            "default": 120,
            "advanced": True,
            "unit": "秒",
        },
    },
})
