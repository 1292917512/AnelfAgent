"""音源库实体的数据契约（pydantic 模型）。

ingest 契约同时是上游 pipeline（文件监听 + FunASR 服务）的对接规范：
    POST /api/entity/voiceprint/ingest
    Header: X-Ingest-Token: <实体配置 voiceprint_ingest_token>
    Body: IngestPayload（见下）

FunASR 服务契约（实体主动拉取时，配置 voiceprint_funasr_endpoint）：
    POST {endpoint}/transcribe（multipart 音频文件，字段名 file）
    响应 JSON 同 IngestPayload 的 segments 结构
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
        default=None, description="192 维声纹向量（cam++），缺省时该段不参与识别")
    abs_start_ms: Optional[int] = Field(
        default=None, description="绝对起始时刻（epoch 毫秒，FunASR 按 source_time 换算）")
    abs_end_ms: Optional[int] = Field(
        default=None, description="绝对结束时刻（epoch 毫秒）")
    part_start_ms: int = Field(
        default=0, ge=0, description="本批在整体合并音频中的起点（源音源回听定位用）")


class IngestPayload(BaseModel):
    """一次音频处理结果的推送载荷。"""

    source_file: str = Field(default="", description="原始音频文件路径（NAS/合并临时文件）")
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


class IdentifyCandidate(BaseModel):
    """识别候选说话人。"""

    id: int
    speaker_key: str
    name: str
    role: str
    status: str
    threshold: float
    similarity: float
    matched: bool


class SpeakerUpdateRequest(BaseModel):
    """说话人档案编辑（全部可选，仅更新出现的字段）。"""

    name: Optional[str] = None
    role: Optional[str] = None
    status: Optional[str] = Field(default=None, pattern="^(confirmed|pending)$")
    threshold: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    notes: Optional[str] = None
    device_source: Optional[str] = None


class ConfirmRequest(BaseModel):
    """临时说话人确认。"""

    name: str = Field(min_length=1)
    role: str = ""


class MergeRequest(BaseModel):
    """身份合并：source_id 并入 target_id。"""

    source_id: int
    target_id: int


class EnrollRequest(BaseModel):
    """注册正式说话人（向量直传或经音频端点）。"""

    name: str = Field(min_length=1)
    vector: List[float] = Field(min_length=1)
    role: str = ""
    notes: str = ""
    device_source: str = ""


class ImportItem(BaseModel):
    """冷启动批量导入项。"""

    name: str = Field(min_length=1)
    vectors: List[List[float]] = Field(min_length=1, description="同一说话人的多条声纹样本")
    role: str = ""
    notes: str = ""


class ImportRequest(BaseModel):
    """冷启动批量导入载荷。"""

    items: List[ImportItem] = Field(min_length=1)


class VectorIdentifyRequest(BaseModel):
    """向量级识别请求（上游自行提向量时）。"""

    vector: List[float] = Field(min_length=1)
    top_k: int = Field(default=5, ge=1, le=20)


class SegmentUpdateRequest(BaseModel):
    """片段编辑（归属改派 / 转写文本修订）。"""

    speaker_id: Optional[int] = Field(default=None, description="目标说话人 id，null = 标记未知")
    transcript: Optional[str] = Field(default=None, description="修订后的转写文本")


class TranscriptReplaceRequest(BaseModel):
    """批量查找替换转写文本。"""

    find: str = Field(min_length=1)
    replace: str = Field(default="")
    speaker_id: Optional[int] = None
    time_from: str = ""
    time_to: str = ""
    limit: int = Field(default=500, ge=1, le=2000)
    dry_run: bool = False


class SegmentMergeRequest(BaseModel):
    """合并多个相邻片段为一条。"""

    ids: List[int] = Field(min_length=2)
    transcript: Optional[str] = Field(
        default=None, description="自定义合并文本，缺省按序拼接")
    speaker_id: Optional[int] = Field(
        default=None, description="指定归属说话人，缺省取首条归属")


class SegmentSplitRequest(BaseModel):
    """拆段请求。"""

    at_ms: int = Field(description="切点（批内毫秒，须在片段区间内）")
    text_first: Optional[str] = None
    text_second: Optional[str] = None
    speaker_second_id: Optional[int] = Field(
        default=None, description="次段归属；未提供字段时继承原归属")


class SegmentAddRequest(BaseModel):
    """手动新增段落。"""

    text: str = Field(min_length=1)
    speaker_id: Optional[int] = None
    ts: Optional[int] = Field(default=None, description="绝对时间（epoch 秒），缺省取录制基准+偏移")
    recording_path: str = ""
    start_ms: int = Field(default=0, ge=0)
    end_ms: int = Field(default=0, ge=0)
    part_start_ms: int = Field(default=0, ge=0)
