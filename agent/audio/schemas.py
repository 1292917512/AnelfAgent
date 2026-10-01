"""音频核心能力的数据契约（pydantic 模型）。

ingest 契约同时是外部音源（文件同步组件、上游 pipeline）的对接规范：
    POST /api/entity/audiosync/ingest
    Header: X-Ingest-Token: <同步组件配置 audiosync_ingest_token>
    Body: IngestPayload（见下）

转写提供者契约（ASR 组件返回结构）：
    [{"start_ms", "end_ms", "text", "vector", "abs_start_ms", "abs_end_ms"}]
"""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field


class SegmentIn(BaseModel):
    """单条语音片段（VAD 切分 + 转写 + 声纹向量）。"""

    start_ms: int = Field(default=0, ge=0, description="批内起始毫秒")
    end_ms: int = Field(default=0, ge=0, description="批内结束毫秒")
    text: str = Field(default="", description="转写文本")
    vector: Optional[List[float]] = Field(
        default=None, description="声纹向量（维度随嵌入模型），缺省时该段不参与识别")
    abs_start_ms: Optional[int] = Field(
        default=None, description="绝对起始时刻（epoch 毫秒，按 source_time 换算）")
    abs_end_ms: Optional[int] = Field(
        default=None, description="绝对结束时刻（epoch 毫秒）")
    part_start_ms: int = Field(
        default=0, ge=0, description="本批在整体合并音频中的起点（源音源回听定位用）")


class IngestPayload(BaseModel):
    """一次音频处理结果的推送载荷。"""

    source_file: str = Field(default="", description="原始音频文件路径")
    recording_path: str = Field(default="", description="所属录制单元（文件夹路径），镜像同步的归属键")
    device_source: str = Field(default="", description="录音设备来源标识")
    ts: Optional[int] = Field(default=None, description="音频发生时间（epoch 秒），缺省取当前")
    segments: List[SegmentIn] = Field(default_factory=list)


class IngestResultItem(BaseModel):
    """单片段入库结果。"""

    segment_id: int
    speaker_id: Optional[int]
    speaker_key: str
    speaker_name: str
    similarity: float
    is_new_speaker: bool


class IngestResult(BaseModel):
    """ingest 批处理结果。"""

    ingested: int
    skipped: int
    results: List[IngestResultItem]


class SpeakerUpdateRequest(BaseModel):
    """说话人档案编辑（全部可选，仅更新出现的字段）。"""

    name: Optional[str] = None
    role: Optional[str] = None
    status: Optional[str] = Field(default=None, pattern="^(confirmed|pending)$")
    threshold: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    notes: Optional[str] = None
    device_source: Optional[str] = None


class SpeakerBindRequest(BaseModel):
    """声纹身份 ↔ 实体画像绑定（空 scope 解绑）。"""

    entity_scope: str = Field(default="", description="实体 scope（user:/group:/agent: 前缀），空串解绑")


class ConfirmRequest(BaseModel):
    """临时说话人确认。"""

    name: str = Field(min_length=1)
    role: str = ""


class MergeRequest(BaseModel):
    """身份合并：source_id 并入 target_id。"""

    source_id: int
    target_id: int


class ImportItem(BaseModel):
    """冷启动批量导入项。"""

    name: str = Field(min_length=1)
    vectors: List[List[float]] = Field(min_length=1, description="同一说话人的多条声纹样本")
    role: str = ""
    notes: str = ""


class MarkReadRequest(BaseModel):
    """批量已读/未读标记（ids 为空 = 全部）。"""

    segment_ids: Optional[List[int]] = None
    read: bool = True


class SegmentUpdateRequest(BaseModel):
    """片段编辑（归属改派 / 转写文本修订）。"""

    speaker_id: Optional[int] = Field(default=None, description="目标说话人 id，null = 标记未知")
    transcript: Optional[str] = Field(default=None, description="修订后的转写文本")

