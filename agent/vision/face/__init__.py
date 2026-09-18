"""人脸子系统 — 核心视觉的眼睛记忆（人物档案 / 识别匹配 / 实体绑定 / 自动召回）。

与声纹子系统（agent/audio）同一范式：引擎（检测+512 维向量提取）归外部
自部署服务（InsightFace buffalo_l + ONNX GPU，经 engine.py HTTP 客户端），
库与匹配治理（人物档案/样本池/锚折叠/AS-Norm 分离度门/合并整理）归 Agent
（store.py 独立 SQLite 卷 "face" + matcher.py）。

召回链路：画面识别命中已绑定实体的人物 → [face_scope:…] 标签（ingest 写
会话历史一次性通知）→ recollection 解析 → 该实体的画像/记忆/关系自动召回；
FaceStatusProvider 每轮注入人物名单/绑定/近期在场摘要。

模块划分：
- engine.py：外部识别服务 HTTP 客户端（/health + /extract，结构化错误契约）
- store.py：FaceStore 人脸核心库（persons / face_samples / face_events）
- matcher.py：匹配引擎（锚扫描 + 双判据评分 + 分离度门 + 建档/合并）
- vectors.py：向量代数（复用声纹加权质心 + 人脸质量权重）
- ingest.py：图片入库管线（检测→过滤→识别→事件→face_scope 打标）
- worker.py：后台串行识别 worker（消息管线/视觉缓冲 fire-and-forget 投递）
- consolidate.py：离线治理（锚聚类合并 + 低价值路人清理）
- context.py / tools.py：上下文注入与 AI 工具面（bootstrap 激活）
"""

from agent.vision.face.store import FaceStore, get_face_store
from core.config import register_configs_safe
from core.provider_keys import register_provider_key

__all__ = [
    "FaceStore",
    "get_face_store",
]

# 组件凭据：外部人脸识别服务地址（视觉域卡片，凭据中心三面等价配置）
register_provider_key(
    "face", domain="vision", title="人脸识别服务",
    description="自部署人脸识别服务地址（SCRFD 检测 + ArcFace 512 维提取，"
                "参考 deploy/face_server 部署）",
    domains=["vision"],
    extra_fields=[{"key": "face_endpoint", "label": "服务地址",
                   "default": "", "secret": False}],
)

# 核心配置项：分组名 vision/face，配置中心自动可见
register_configs_safe({
    "vision/face": {
        "face_ai_enabled": {
            "description": "是否允许 AI 调用人脸库工具（人物管理/识别/事件检索/治理）",
            "default": True,
        },
        "face_auto_ingest": {
            "description": "频道入站图片自动人脸识别（命中绑定实体时打 face_scope 标驱动记忆召回）",
            "default": True,
        },
        "face_watch_enabled": {
            "description": "视觉源监视帧自动人脸识别（隐私敏感，默认关闭；开启后盯屏画面变化帧参与识别）",
            "default": False,
        },
        "face_context_inject": {
            "description": "是否向 AI 上下文注入人脸库摘要（人物名单/实体绑定/近期在场）",
            "default": True,
        },
        "face_scope_note_enabled": {
            "description": "识别命中绑定实体时向会话历史写 face_scope 一次性通知（自动召回的打标通道）",
            "default": True,
        },
        "face_match_threshold": {
            "description": "人脸匹配阈值（ArcFace 余弦 ≥ 阈值判为已知人；典型区间 0.3~0.5，部署后按实际库标定）",
            "default": 0.40,
            "advanced": True,
            "value_type": "range",
            "min": 0, "max": 1, "step": 0.01,
        },
        "face_separation": {
            "description": "AS-Norm 分离度门槛（z 分值，'很多人都像'的模糊查询降级为临时人物；0=关闭）",
            "default": 2.0,
            "advanced": True,
            "value_type": "range",
            "min": 0, "max": 5, "step": 0.1,
        },
        "face_merge_threshold": {
            "description": "人物合并判读线（锚余弦 ≥ 此值视为同一人分裂档案，比匹配阈值宽松）",
            "default": 0.55,
            "advanced": True,
            "value_type": "range",
            "min": 0, "max": 1, "step": 0.01,
        },
        "face_coherence_floor": {
            "description": "样本入池相干门限（与锚余弦低于此值拒入，防错认人/劣质脸投毒）",
            "default": 0.20,
            "advanced": True,
            "value_type": "range",
            "min": 0, "max": 1, "step": 0.01,
        },
        "face_max_samples_per_person": {
            "description": "每人物人脸样本池上限（池满优先淘汰同来源最早样本，保持采集多样性）",
            "default": 10,
            "advanced": True,
            "unit": "条",
        },
        "face_min_det_score": {
            "description": "参与识别建档的最低检测置信度（低于此值的小脸/糊脸不识别）",
            "default": 0.55,
            "value_type": "range",
            "min": 0, "max": 1, "step": 0.05,
        },
        "face_min_face_px": {
            "description": "参与识别建档的人脸框短边下限（像素；0=不限）",
            "default": 64,
            "advanced": True,
            "unit": "px",
        },
        "face_auto_create_unknown": {
            "description": "识别未命中已知人时自动创建临时人物档案（pending 待确认）",
            "default": True,
            "advanced": True,
        },
        "face_auto_accumulate": {
            "description": "识别命中已知人时自动累积人脸样本（锚随采样进化）",
            "default": True,
            "advanced": True,
        },
        "face_engine_timeout": {
            "description": "人脸识别服务调用超时",
            "default": 30,
            "unit": "s", "min": 5, "max": 120,
        },
        "face_event_retention_days": {
            "description": "画面出现事件保留天数（过期自动清理，含图片引用；0=不限）",
            "default": 30,
            "unit": "天", "min": 0, "max": 365,
        },
        "face_note_cooldown_s": {
            "description": "同会话同人物的 face_scope 历史打标冷却（防连续图片刷屏轰炸；0=不节流）",
            "default": 300,
            "advanced": True,
            "unit": "s", "min": 0, "max": 3600,
        },
        "face_insignificant_max_matches": {
            "description": "低价值临时人物判定线（命中次数 ≤ 此值视为路人，整理时可清理）",
            "default": 2,
            "advanced": True,
        },
    },
})
