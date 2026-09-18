"""人脸向量代数：复用声纹库的加权质心代数 + 人脸专属质量权重。

锚（anchor）是人物人脸的代表向量——全部历史合格样本的质量加权质心。
加权质心满足结合律（任意顺序/分组折叠与一次性全量加权平均一致），
锚可随采样增量进化、可跨档案精确合并，无需重放历史。

与声纹的唯一差异是权重来源：声纹按语音时长计（长语音嵌入更稳定），
人脸按检测置信度 × 正脸程度计（正脸高清的 ArcFace 嵌入更可靠）。
"""

from __future__ import annotations

from typing import Any, Dict, Optional

# 共享代数（cosine/cosine_many/unit_rows/pairwise_sims/blend/weighted_centroid）
# 与声纹库同一实现——加权质心代数与模态无关，不重复造轮子
from agent.audio.vectors import (  # noqa: F401
    blend,
    cosine,
    cosine_many,
    pairwise_sims,
    unit_rows,
    weighted_centroid,
)


def quality_weight(det_score: float, pose: Optional[Dict[str, Any]] = None) -> float:
    """人脸样本权重 = 检测置信度 × 正脸系数，截断到 [0.5, 2.0]。

    正脸系数按偏航/俯仰偏离线性衰减（±60° 以上按半权计）：
    侧脸/低头的人脸嵌入质量显著低于正脸，锚折叠时贡献相应降低。
    """
    frontal = 1.0
    if pose:
        deviation = max(abs(float(pose.get("yaw", 0.0))),
                        abs(float(pose.get("pitch", 0.0))))
        frontal = max(0.5, 1.0 - min(1.0, deviation / 60.0))
    weight = float(det_score) * (0.5 + frontal)
    return min(2.0, max(0.5, weight))
