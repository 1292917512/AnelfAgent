"""入库管线：图片 → 人脸检测/识别建档 → 出现事件落库 → face_scope 打标。

数据流（对应一张图片的处理）：
    ingest_image(path, source, scope)
      → engine.extract_faces（外部 GPU 服务：SCRFD 检测 + ArcFace 512 维提取；
          引擎未配置/不可达 fail-open，只记日志返回 skipped 结果）
      → 质量过滤（det_score < face_min_det_score 或脸框短边 < face_min_face_px
          的脸不参与识别建档——小脸/糊脸的嵌入不可靠）
      → 逐脸 matcher.identify（已知人命中回写 + 样本累积/锚折叠
          / 新人建临时档案 fc_tmp_XXXX，受 face_auto_create_unknown）
      → store.add_event（一图一行，未读收件箱 +1，顺带按保留期清理）
      → face_scope 打标：scope 非空且命中已绑定实体的人物时，向该会话
          对话历史追加一次性 system 消息（含 [face_scope:…] 标签，
          trigger_mind=False）——recollection 扫描对话尾部即自动召回
          该实体的画像/记忆/关系（与声纹 speaker_scope 同一召回机制）。
          节流：同 (scope, person) 冷却窗口内不重复写（刷屏图片防轰炸）。
"""

from __future__ import annotations

import os
import shutil
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.config import get_config_bool, get_config_float, get_config_int
from core.log import log

from . import engine, matcher
from .schemas import FaceDetection, FaceHit, IngestImageResult
from .store import FaceStore, get_face_store

_LOG_TAG = "人脸"

# face_scope 历史打标节流：{(scope, person_id): 最近写入的单调时刻}
_note_throttle: Dict[tuple[str, int], float] = {}


def min_det_score() -> float:
    """参与识别建档的最低检测置信度（face_min_det_score，默认 0.55）。"""
    return get_config_float("face_min_det_score", 0.55)


def min_face_px() -> int:
    """参与识别建档的人脸框短边下限（face_min_face_px，默认 64px）。"""
    return max(0, get_config_int("face_min_face_px", 64))


def note_cooldown_s() -> int:
    """同 (会话, 人物) 的 face_scope 历史打标冷却秒数（默认 300，0=不节流）。"""
    return max(0, get_config_int("face_note_cooldown_s", 300))


def passes_quality(face: FaceDetection) -> bool:
    if face.det_score < min_det_score():
        return False
    limit = min_face_px()
    return limit <= 0 or face.face_px >= limit


def face_upload_dir() -> Path:
    """人脸引用图片的持久目录（uploads/face/，Web 缩略图服务白名单内）。"""
    from core.path import ConfigPaths
    dest = Path(str(ConfigPaths.UPLOAD_DIR)) / "face"
    dest.mkdir(parents=True, exist_ok=True)
    return dest


def persist_image(image_path: str) -> str:
    """把事件/样本引用的图片落到持久目录，返回持久路径（幂等）。

    入库引用必须是持久路径：生成图/临时文件/频道上传都可能被清理，
    悬空引用会让 Web 缩略图 404（图已删、事件还在）。已在持久目录内
    原样返回；复制失败 fail-open 返回原路径（识别流程不受影响）。
    """
    src = Path(image_path)
    try:
        dest_root = face_upload_dir()
        if os.path.realpath(str(src.parent)) == os.path.realpath(str(dest_root)):
            return image_path
        dest = dest_root / f"{int(time.time() * 1000)}_{src.name}"
        shutil.copyfile(src, dest)
        return str(dest)
    except OSError as exc:
        log(f"人脸引用图片持久化失败（沿用原路径）: {exc}", "DEBUG", tag=_LOG_TAG)
        return image_path


def _hit_from(identified: Dict[str, Any], face: FaceDetection) -> FaceHit:
    person = identified.get("person") or {}
    return FaceHit(
        person_id=person.get("id"),
        person_key=str(person.get("person_key", "")),
        person_name=str(person.get("name", "")),
        entity_scope=str(person.get("entity_scope", "")),
        similarity=float(identified.get("similarity", 0.0)),
        is_new=bool(identified.get("is_new", False)),
        matched=bool(person) and not identified.get("is_new", False),
        det_score=face.det_score,
        bbox=list(face.bbox),
        sample_added=bool(identified.get("sample_added", False)),
    )


async def ingest_image(
    image_path: str,
    source: str = "manual",
    *,
    scope: str = "",
    store: Optional[FaceStore] = None,
    ts_ns: Optional[int] = None,
) -> IngestImageResult:
    """处理一张图片：检测 → 过滤 → 逐脸识别 → 事件落库 → 打标。

    Args:
        image_path: 本地图片绝对路径（URL 由调用方先落盘）。
        source: 来源标注（inbound=频道图片 / vision:<key>=视觉源帧 /
            web=面板上传 / manual=工具调用）。
        scope: 触发会话的 entity_scope（非空且命中绑定实体时打 face_scope 标）。
    """
    store = store or get_face_store()
    # 引用持久化：事件/样本只存持久路径，防源文件（生成图/临时文件）被清理后缩略图 404
    image_path = persist_image(image_path)
    result = IngestImageResult(image_path=image_path, source=source)

    if not engine.is_configured():
        result.skipped = True
        result.error = "人脸引擎未配置（组件凭据 face.face_endpoint）"
        return result
    try:
        extracted = await engine.extract_faces(
            image_path, min_det_score=min_det_score())
    except engine.FaceEngineNotConfigured as exc:
        result.skipped = True
        result.error = str(exc)
        return result
    except engine.FaceEngineError as exc:
        # fail-open：引擎故障不阻塞调用链（消息管线/视觉缓冲），只记日志
        log(f"人脸提取失败 {image_path[:80]}: {exc}", "DEBUG", tag=_LOG_TAG)
        result.skipped = True
        result.error = str(exc)
        return result

    result.faces_detected = len(extracted.faces)
    qualified = [f for f in extracted.faces if f.vector and passes_quality(f)]
    result.faces_filtered = len(extracted.faces) - len(qualified)
    if not qualified:
        result.skipped = True
        return result

    if ts_ns is None:
        ts_ns = time.time_ns()
    sample_ids: List[int] = []
    for face in qualified:
        identified = await matcher.identify(
            store, face.vector,
            det_score=face.det_score, bbox=list(face.bbox),
            pose=face.pose.model_dump(), image_path=image_path,
            source=source, ts_ns=ts_ns)
        result.hits.append(_hit_from(identified, face))
        sid = int(identified.get("sample_id", -1))
        if sid > 0:
            sample_ids.append(sid)

    person_ids = [int(h.person_id) for h in result.hits if h.person_id is not None]
    faces_json = [h.model_dump() for h in result.hits]
    result.event_id = await store.add_event(
        image_path=image_path, source=source,
        width=extracted.width, height=extracted.height,
        faces=faces_json, person_ids=person_ids, ts_ns=ts_ns)
    await store.attach_samples_to_event(sample_ids, int(result.event_id))

    if scope and get_config_bool("face_scope_note_enabled", True):
        result.note_written = await _write_scope_note(scope, result, source)
    return result


async def _write_scope_note(
    scope: str, result: IngestImageResult, source: str,
) -> bool:
    """命中绑定实体的人物 → 向会话历史追加 face_scope 一次性通知。

    写入对话历史（system，trigger_mind=False）而非短期记忆：一次性事实
    随窗口自然滚动、追加在尾部前缀稳定；recollection 扫描对话尾部解析
    face_scope 标签即驱动画像/记忆/关系召回。失败 fail-open 返回 False。
    """
    bound = [(h.person_id, h.entity_scope, h.person_name)
             for h in result.hits
             if h.entity_scope and h.person_id is not None
             and not h.entity_scope.startswith("agent:")]
    if not bound:
        return False
    cooldown = note_cooldown_s()
    now = time.monotonic()
    fresh = []
    for person_id, entity_scope, name in bound:
        key = (scope, int(person_id))
        if cooldown and now - _note_throttle.get(key, 0.0) < cooldown:
            continue
        _note_throttle[key] = now
        fresh.append((int(person_id), entity_scope, name))
    if not fresh:
        return False

    try:
        from agent.mind.tools.ports import mind_port
        if not mind_port.bound:
            return False
        from agent.mind.tools.scheduler import _append_one_shot_history
        from core.tags import tag_label
        mind = mind_port.get()
        names = "、".join(nm or f"人物{pid}" for pid, _, nm in fresh)
        tags = "".join(tag_label("face_scope", sc) for _, sc, _ in fresh)
        prompt = (f"[人脸识别·一次性通知] 画面（来源 {source or '未知'}）中出现: "
                  f"{names} {tags}")
        return await _append_one_shot_history(
            mind.pfc, scope, mind.pfc.get_adapter_key(scope), prompt)
    except Exception as exc:
        log(f"face_scope 历史打标失败（已忽略）: {exc}", "DEBUG", tag=_LOG_TAG)
        return False
