"""音源同步（AudioSync）实体 — 外部音源接入核心音频库的同步组件。

职责边界：本实体只做"音源 → 核心音频库"的搬运与预处理——
- 目录镜像同步（本地目录 / OpenList，来源组件化可扩展）：扫描 → 合并 →
  转写（经核心 ASR 提供者链）→ 推送核心入库管线（声纹识别在核心完成）
- 外部音源推送接入（POST /api/entity/audiosync/ingest，X-Ingest-Token 鉴权）
- FunASR 组件注册（ASR 转写 + 声纹提取提供者接入核心注册表）
- 音源取回器注册（核心回听定位原始音频时的远程下载通道）

说话人档案、片段库、识别检索等音频库本体是核心音频能力（agent.audio），
AI 工具组 audio 与 Web 音频页签提供完整管理面。

目录名 / group 名 / 面板名 / 路由名统一为 audiosync，框架各发现机制自然对齐：
- @entity: 注册 group（被 discover_entities 扫描 tools.py 时触发）
- entity_manifest: 自报展示信息（名称/图标/排序）
- register_configs_safe: 实体配置项（实体详情页配置 tab 展示）
- register_lifecycle: 启动钩子（被 discover_entity_lifecycles 扫描）
- router.py: build_router()（被 _mount_entity_routers 挂载到 /api/entity/audiosync）
- panel.tsx: 实体管理面板（被 entity-panels glob 发现）
"""

from core.config import register_configs_safe
from entities._sdk import entity, entity_manifest

entity("audiosync", "音源同步 - 外部音源（目录/推送）接入核心音频库的同步组件")

entity_manifest(
    display_name="音源同步",
    icon="audio-lines",
    description="外部音源接入：目录镜像同步（本地/OpenList 组件化）+ 上游推送 + FunASR 转写组件",
    version="1.0.0",
    order=46,
    group="audiosync",
)

# 实体配置项：分组名 entity/audiosync，实体详情页配置 tab 自动展示
register_configs_safe({
    "entity/audiosync": {
        "audiosync_ingest_token": {
            "description": "外部音源推送令牌（X-Ingest-Token 头，留空则 /ingest 关闭）",
            "default": "",
        },
        "audiosync_outbound_webhook_url": {
            "description": "入库摘要出站 webhook 地址（留空不推送）",
            "default": "",
        },
        "audiosync_watch_enabled": {
            "description": "是否启用目录自动同步（周期扫描，新增文件自动转写入库）",
            "default": False,
        },
        "audiosync_watch_paused": {
            "description": "是否暂停同步（周期扫描与手动同步都暂停，其余功能不受影响）",
            "default": False,
        },
        "audiosync_watch_dir": {
            "description": "本地音频监听目录（NAS 挂载点；留空且已配置 OpenList 时走 OpenList）",
            "default": "",
        },
        "audiosync_watch_recursive": {
            "description": "是否递归扫描子目录",
            "default": True,
        },
        "audiosync_watch_interval_seconds": {
            "description": "目录扫描周期",
            "default": 60,
            "advanced": True,
            "unit": "秒",
        },
        "audiosync_watch_max_per_scan": {
            "description": "单轮扫描最多处理的录制单元数（防积压时长时间占用）",
            "default": 50,
            "advanced": True,
            "unit": "个",
        },
        "audiosync_error_retry_seconds": {
            "description": "失败单元的自动重试冷却（超时后即使内容未变也重试，"
                           "自愈 FunASR 重启/网络抖动等瞬时故障）",
            "default": 3600,
            "advanced": True,
            "unit": "秒",
        },
        "audiosync_watch_exclude": {
            "description": "同步排除规则（逗号分隔 glob，如 tmp_*,*.tmp,*测试*；"
                           "命中项不同步且不参与镜像删除）",
            "default": "",
        },
        "audiosync_audio_extensions": {
            "description": "纳入同步的音频/视频扩展名（逗号分隔）",
            "default": ".wav,.mp3,.m4a,.flac,.ogg,.amr,.wma,.aac,.mp4,.mkv,.mov",
        },
        "audiosync_openlist_endpoint": {
            "description": "OpenList 服务地址（如 http://nas:5244，配置后优先于本地目录）",
            "default": "",
        },
        "audiosync_openlist_token": {
            "description": "OpenList API 令牌（Authorization 头）",
            "default": "",
        },
        "audiosync_openlist_path": {
            "description": "OpenList 监听根路径",
            "default": "/",
        },
        "audiosync_merge_max_seconds": {
            "description": "单批音频最大时长（静音截断无法命中时的硬上限）",
            "default": 600,
            "advanced": True,
            "unit": "秒",
        },
        "audiosync_merge_min_seconds": {
            "description": "单批音频最小时长（静音截断下限，过短尾巴并入前一批）",
            "default": 60,
            "advanced": True,
            "unit": "秒",
        },
        "audiosync_split_silence_db": {
            "description": "静音截断的噪音阈值（低于此音量且持续达标的区间作为切点）",
            "default": -40.0,
            "advanced": True,
            "unit": "dB",
        },
        "audiosync_split_silence_min_s": {
            "description": "静音截断的最小时长（短于此的停顿不作为切点）",
            "default": 1.0,
            "advanced": True,
            "unit": "秒",
        },
        "audiosync_silence_skip_db": {
            "description": "空音跳过的平均音量阈值（低于此值不参与合并，0=关闭空音检测）",
            "default": -45.0,
            "advanced": True,
            "unit": "dB",
        },
    }
})


def register_lifecycle() -> None:
    """注册目录同步引擎的生命周期（启动同步循环，退出关闭）。"""
    from core.lifecycle import Lifecycle

    from .watcher import get_audiosync_watcher
    watcher = get_audiosync_watcher()
    Lifecycle.register("audiosync_watcher", watcher,
                       cleanup=watcher.close, on_start=watcher.start)


from .sources import load_sources  # noqa: E402

load_sources()  # 扫描注册同步来源组件（本地目录/OpenList）

from . import fetch as _fetch  # noqa: F401,E402  # 注册核心回听的音源取回器
from . import tools as _tools  # noqa: F401,E402  # 触发 @tool 注册
from .funasr_provider import register_providers as _register_audio_providers  # noqa: E402

_register_audio_providers()  # FunASR 组件接入核心音频注册表

from .funasr_stream import register_streaming_provider as _register_stream  # noqa: E402

_register_stream()  # 流式 ASR 组件接入核心注册表


from entities._sdk import register_provider_key  # noqa: E402

register_provider_key(
    "funasr", domain="sound", title="FunASR 转写服务",
    description="自部署转写服务地址（转写/流式转写/声纹提取）",
    domains=["sound"],
    extra_fields=[{"key": "funasr_endpoint", "label": "服务地址",
                   "default": "", "secret": False}],
)
