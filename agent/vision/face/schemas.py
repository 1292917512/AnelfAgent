"""人脸子系统的数据契约（pydantic 模型）。

引擎契约同时是外部人脸识别服务（Windows GPU 机自部署）的对接规范：
    GET  {endpoint}/health   → {"status","model","dim","device","version"}
    POST {endpoint}/extract  → {"width","height","faces":[FaceDetection]}
错误响应统一 {"error": {"code", "message"}}（码表见 engine.py）。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class FacePose(BaseModel):
    """人脸姿态角（度；正脸为 0，绝对值越大越侧）。"""

    pitch: float = 0.0
    yaw: float = 0.0
    roll: float = 0.0


class FaceDetection(BaseModel):
    """引擎返回的单张人脸（检测框 + 质量 + 姿态 + 512 维 L2 归一化向量）。"""

    bbox: List[float] = Field(default_factory=list, description="[x, y, w, h] 像素")
    det_score: float = Field(default=0.0, description="检测置信度 0..1")
    pose: FacePose = Field(default_factory=FacePose)
    vector: List[float] = Field(default_factory=list, description="L2 归一化人脸向量")

    @property
    def face_px(self) -> float:
        """人脸框短边像素（小脸过滤依据）。"""
        return min(self.bbox[2], self.bbox[3]) if len(self.bbox) >= 4 else 0.0


class ExtractResult(BaseModel):
    """一次图片人脸提取结果。"""

    width: int = 0
    height: int = 0
    faces: List[FaceDetection] = Field(default_factory=list)


class EngineHealth(BaseModel):
    """引擎 /health 应答（可观测性：模型归属/向量维度/推理设备/加载态）。"""

    status: str = ""
    model: str = ""
    dim: int = 0
    device: str = ""
    version: str = ""
    loaded: Optional[bool] = None


class FaceHit(BaseModel):
    """入库管线中单张人脸的识别归属。"""

    person_id: Optional[int] = None
    person_key: str = ""
    person_name: str = ""
    entity_scope: str = ""
    similarity: float = 0.0
    is_new: bool = False
    matched: bool = False
    det_score: float = 0.0
    bbox: List[float] = Field(default_factory=list)
    sample_added: bool = False


class IngestImageResult(BaseModel):
    """单图入库结果。"""

    image_path: str = ""
    source: str = ""
    faces_detected: int = 0
    faces_filtered: int = 0
    event_id: Optional[int] = None
    hits: List[FaceHit] = Field(default_factory=list)
    error: str = ""
    skipped: bool = False
    note_written: bool = False

    @property
    def bound_scopes(self) -> List[str]:
        """命中且已绑定实体的 scope 列表（face_scope 打标依据）。"""
        seen: List[str] = []
        for hit in self.hits:
            if hit.entity_scope and hit.entity_scope not in seen:
                seen.append(hit.entity_scope)
        return seen


class PersonBrief(BaseModel):
    """匹配结果中的人物简报。"""

    id: int
    person_key: str
    name: str
    role: str
    status: str
    entity_scope: str = ""
    threshold: float = 0.0


class IdentifyCandidate(BaseModel):
    """识别候选（双判据评分：锚/最佳样本 + 分离度）。"""

    id: int
    person_key: str
    name: str
    role: str
    status: str
    threshold: float
    similarity: float
    matched: bool
    anchor_similarity: float = 0.0
    sample_similarity: float = 0.0
    separation: Optional[float] = None
    entity_scope: str = ""


class IdentifyResult(BaseModel):
    """identify 返回结构（与音频库同构，消费面统一）。"""

    person: Optional[Dict[str, Any]] = None
    similarity: float = 0.0
    is_new: bool = False
    sample_added: bool = False
    candidates: List[Dict[str, Any]] = Field(default_factory=list)
