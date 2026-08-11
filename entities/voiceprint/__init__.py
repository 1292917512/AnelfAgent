"""音源库（Voiceprint）实体。

本地说话人声纹库 + 语音转写片段库，供 AI 与 Web 用户共同使用：
- 声纹库：192 维向量存储与余弦检索、多样本池（FIFO 淘汰）、全局/单人阈值判定、
  临时说话人自动建档与确认、身份合并
- 片段库：转写文本 FTS5 + 语义向量混合检索，按说话人/时间段硬过滤
- 接入：上游 pipeline 推送（/ingest，X-Ingest-Token 鉴权）或实体主动拉取
  （可配置 FunASR HTTP 服务），出站 webhook 推送入库摘要
- 上下文：说话人名单与未读动态经 context_provider 注入 AI
- 面板：panel.tsx 管理界面（说话人/检索/入库/设置）

目录名 / group 名 / 面板名 / 路由名统一为 voiceprint，框架各发现机制自然对齐：
- @entity: 注册 group（被 discover_entities 扫描 tools.py 时触发）
- entity_manifest: 自报展示信息（名称/图标/排序）
- register_configs_safe: 实体配置项（实体详情页配置 tab 展示）
- register_lifecycle: 启动钩子（被 discover_entity_lifecycles 扫描）
- router.py: build_router()（被 _mount_entity_routers 挂载到 /api/entity/voiceprint）
- panel.tsx: 实体管理面板（被 entity-panels glob 发现）
"""

from core.config import register_configs_safe
from entities._sdk import entity, entity_manifest

entity("voiceprint", "音源库 - 说话人声纹识别与档案管理、语音转写记录检索")

entity_manifest(
    display_name="音源库",
    icon="audio-lines",
    description="说话人声纹库：识别/归属管理/合并，语音转写语义检索，上游 pipeline 对接",
    version="1.0.0",
    order=40,
    group="voiceprint",
)

# 实体配置项：分组名 entity/voiceprint，实体详情页配置 tab 自动展示
register_configs_safe({
    "entity/voiceprint": {
        "voiceprint_ai_enabled": {
            "description": "是否允许 AI 调用音源库工具（说话人管理/识别/检索）",
            "default": True,
        },
        "voiceprint_match_threshold": {
            "description": "声纹匹配阈值（相似度 ≥ 阈值判为已知人）",
            "default": 0.75,
            "advanced": True,
            "value_type": "range",
            "min": 0,
            "max": 1,
            "step": 0.05,
        },
        "voiceprint_merge_threshold": {
            "description": "合并阈值：离线整理时质心相似度 ≥ 此值的临时说话人"
                           "建议合并（比匹配阈值宽松）",
            "default": 0.70,
            "advanced": True,
            "value_type": "range",
            "min": 0,
            "max": 1,
            "step": 0.05,
        },
        "voiceprint_insignificant_max_matches": {
            "description": "低价值判定：命中次数 ≤ 此值（配合时长条件；"
                           "智能合并时可一并清理）",
            "default": 2,
            "advanced": True,
            "unit": "次",
        },
        "voiceprint_insignificant_max_audio_ms": {
            "description": "低价值判定：累计音频时长上限"
                           "（与命中次数同时满足才算低价值）",
            "default": 5000,
            "advanced": True,
            "unit": "毫秒",
        },
        "voiceprint_max_samples_per_speaker": {
            "description": "每说话人声纹样本池上限（超出时按淘汰策略清理）",
            "default": 5,
            "advanced": True,
            "unit": "条",
        },
        "voiceprint_sample_evict_strategy": {
            "description": "样本淘汰策略：outlier=淘汰与质心最不相似的样本"
                           "（噪音新样本也会被拒入）；fifo=淘汰最早样本",
            "default": "outlier",
        },
        "voiceprint_centroid_match": {
            "description": "是否启用质心匹配（得分取 max(最佳样本, 均值向量)，"
                           "抑制单样本噪音提升稳定性）",
            "default": True,
        },
        "voiceprint_auto_accumulate": {
            "description": "是否在命中已知人时自动累积新声纹样本入其样本池",
            "default": True,
        },
        "voiceprint_auto_create_unknown": {
            "description": "是否自动为未匹配人声创建临时说话人（待确认）",
            "default": True,
        },
        "voiceprint_skip_noise_segments": {
            "description": "是否跳过纯标点/空白语音段（不建档不计片段，防噪音建档）",
            "default": True,
        },
        "voiceprint_min_voiceprint_ms": {
            "description": "参与声纹识别的最小段时长（短于此只转写留存、不匹配不建档，0=不限制）",
            "default": 2000,
            "advanced": True,
            "unit": "毫秒",
        },
        "voiceprint_attach_unidentified": {
            "description": "是否将未识别段挂到同录制最近的已归属段"
                           "（关闭则未识别段标记为未知）",
            "default": True,
        },
        "voiceprint_funasr_endpoint": {
            "description": "FunASR 服务地址（如 http://nas:10095），用于音频转写与声纹提取",
            "default": "",
        },
        "voiceprint_funasr_timeout": {
            "description": "FunASR 服务调用超时",
            "default": 120,
            "advanced": True,
            "unit": "秒",
        },
        "voiceprint_ingest_token": {
            "description": "上游 pipeline 推送令牌（X-Ingest-Token 头，留空则 /ingest 关闭）",
            "default": "",
        },
        "voiceprint_outbound_webhook_url": {
            "description": "入库摘要出站 webhook 地址（留空不推送）",
            "default": "",
        },
        "voiceprint_context_inject": {
            "description": "是否向 AI 上下文注入音源库摘要（说话人名单/待确认/未读）",
            "default": True,
        },
        "voiceprint_watch_enabled": {
            "description": "是否启用目录自动同步（周期扫描，新增文件自动转写入库）",
            "default": False,
        },
        "voiceprint_watch_paused": {
            "description": "是否暂停同步（周期扫描与手动同步都暂停，其余功能不受影响）",
            "default": False,
        },
        "voiceprint_watch_dir": {
            "description": "本地音频监听目录（NAS 挂载点；留空且已配置 OpenList 时走 OpenList）",
            "default": "",
        },
        "voiceprint_watch_recursive": {
            "description": "是否递归扫描子目录",
            "default": True,
        },
        "voiceprint_watch_interval_seconds": {
            "description": "目录扫描周期",
            "default": 60,
            "advanced": True,
            "unit": "秒",
        },
        "voiceprint_watch_max_per_scan": {
            "description": "单轮扫描最多处理的录制单元数（防积压时长时间占用）",
            "default": 50,
            "advanced": True,
            "unit": "个",
        },
        "voiceprint_error_retry_seconds": {
            "description": "失败单元的自动重试冷却（超时后即使内容未变也重试，"
                           "自愈 FunASR 重启/网络抖动等瞬时故障）",
            "default": 3600,
            "advanced": True,
            "unit": "秒",
        },
        "voiceprint_watch_exclude": {
            "description": "同步排除规则（逗号分隔 glob，如 tmp_*,*.tmp,*测试*；"
                           "命中项不同步且不参与镜像删除）",
            "default": "",
        },
        "voiceprint_audio_extensions": {
            "description": "纳入同步的音频/视频扩展名（逗号分隔）",
            "default": ".wav,.mp3,.m4a,.flac,.ogg,.amr,.wma,.aac,.mp4,.mkv,.mov",
        },
        "voiceprint_openlist_endpoint": {
            "description": "OpenList 服务地址（如 http://nas:5244，配置后优先于本地目录）",
            "default": "",
        },
        "voiceprint_openlist_token": {
            "description": "OpenList API 令牌（Authorization 头）",
            "default": "",
        },
        "voiceprint_openlist_path": {
            "description": "OpenList 监听根路径",
            "default": "/",
        },
        "voiceprint_ffmpeg_bin": {
            "description": "ffmpeg 可执行文件路径（非 16k 单声道 WAV 自动转码预处理）",
            "default": "ffmpeg",
        },
        "voiceprint_merge_max_seconds": {
            "description": "单批音频最大时长（静音截断无法命中时的硬上限）",
            "default": 600,
            "advanced": True,
            "unit": "秒",
        },
        "voiceprint_merge_min_seconds": {
            "description": "单批音频最小时长（静音截断下限，过短尾巴并入前一批）",
            "default": 60,
            "advanced": True,
            "unit": "秒",
        },
        "voiceprint_split_silence_db": {
            "description": "静音截断的噪音阈值（低于此音量且持续达标的区间作为切点）",
            "default": -40.0,
            "advanced": True,
            "unit": "dB",
        },
        "voiceprint_split_silence_min_s": {
            "description": "静音截断的最小时长（短于此的停顿不作为切点）",
            "default": 1.0,
            "advanced": True,
            "unit": "秒",
        },
        "voiceprint_silence_skip_db": {
            "description": "空音跳过的平均音量阈值（低于此值不参与合并，0=关闭空音检测）",
            "default": -45.0,
            "advanced": True,
            "unit": "dB",
        },
    }
})


def register_lifecycle() -> None:
    """注册存储与目录同步的生命周期（启动建库 + 同步循环，退出关闭）。"""
    from core.lifecycle import Lifecycle

    from .store import get_voiceprint_store
    from .watcher import get_voiceprint_watcher
    store = get_voiceprint_store()
    Lifecycle.register("voiceprint_store", store,
                       cleanup=store.close, on_start=store.initialize)
    watcher = get_voiceprint_watcher()
    Lifecycle.register("voiceprint_watcher", watcher,
                       cleanup=watcher.close, on_start=watcher.start)


from . import context, tools, worker  # noqa: F401, E402  # 注册上下文提供者 + 触发 @tool/backlog 注册
