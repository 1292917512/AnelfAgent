"""视觉服务门面 — web 层与 agent/vision 核心层之间的收口。"""

from __future__ import annotations

import asyncio
import base64
import binascii
import os
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from agent.vision import all_sources, get_vision_buffer, get_vision_watcher
from agent.vision.face import engine as face_engine
from agent.vision.face import get_face_store
from agent.vision.face import matcher as face_matcher
from agent.vision.face.consolidate import consolidate as face_consolidate_run
from agent.vision.face.engine import (  # noqa: F401  # 门面再导出（web 层归因用）
    FaceEngineError,
    FaceEngineNotConfigured,
)

_PUSH_MAX_BYTES = 20 * 1024 * 1024
_PUSH_EXTS = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}
_FACE_UPLOAD_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}


class VisionServiceFacade:
    """视觉页签的数据聚合（监视状态 + 源清单 + 帧推送 + 注入情况）。"""

    def status(self) -> Dict[str, Any]:
        buffer = get_vision_buffer()
        return {
            **get_vision_watcher().status(),
            "latest": {
                "path": buffer.latest.path, "source": buffer.latest.source,
                "captured_at": buffer.latest.captured_at,
            } if buffer.latest else None,
            "last_change_at": buffer.last_change_at or None,
            "injection": self.injection_status(),
        }

    def injection_status(self) -> Dict[str, Any]:
        """画面注入情况（页签展示：开关 + 最近文本/画面注入轨迹）。"""
        from core.context_provider import ContextProviderRegistry
        for meta in ContextProviderRegistry.get_all():
            if meta.group == "vision" and meta.instance is not None:
                instance = meta.instance
                extra = {}
                if hasattr(instance, "injection_status"):
                    extra = instance.injection_status()
                return {
                    "provider": meta.name,
                    "active": ContextProviderRegistry.is_active(meta),
                    **extra,
                }
        return {"provider": "", "active": False}

    def sources(self) -> Dict[str, Any]:
        from agent.vision.framework import is_enabled
        watcher = get_vision_watcher()
        return {
            "sources": [{
                "key": s.key, "display_name": s.display_name,
                "description": s.description,
                "pollable": s.poll_interval > 0 and s.can_capture,
                "can_capture": s.can_capture,
                "enabled": is_enabled(s.key),
                "watching": watcher.watching(s.key),
            } for s in all_sources()],
        }

    def latest_frame(self, source: str = "") -> Optional[str]:
        """最新一帧的文件路径（source 指定源；不存在/文件已删返回 None）。"""
        buffer = get_vision_buffer()
        frame = buffer.latest_by_source.get(source) if source else buffer.latest
        if frame is None or not os.path.isfile(frame.path):
            return None
        return frame.path

    def capabilities(self) -> Dict[str, Any]:
        """视觉能力（理解/图生成/图编辑/视频）的提供者状态与生效优先级链。"""
        from agent.vision.capabilities import VISUAL_CAPABILITIES, get_visual_router
        return get_visual_router().status(list(VISUAL_CAPABILITIES))

    async def watch(self, action: str, source: str, interval: float = 0) -> Dict[str, Any]:
        """监视开关与源启停（页签控制；与 vision_watch/vision_source_set 工具同语义）。"""
        from agent.vision.framework import is_enabled, set_enabled
        watcher = get_vision_watcher()
        if action == "enable":
            set_enabled(source, True)
            return {"watching": watcher.watching_sources(), "enabled": True}
        if action == "disable":
            set_enabled(source, False)
            if watcher.watching(source):
                await watcher.stop(source)
            return {"watching": watcher.watching_sources(), "enabled": False}
        if action == "start":
            if not is_enabled(source):
                raise ValueError(f"视觉源已停用: {source}（先激活再监视）")
            if interval > 0:
                from core.config import ConfigManager
                ConfigManager.set("vision_watch_interval_s", interval)
                ConfigManager.save()
            error = await watcher.start(source)
            if error:
                raise ValueError(error)
        elif action == "stop":
            await watcher.stop(source)
        elif action != "status":
            raise ValueError(f"未知 action: {action}")
        return {"watching": watcher.watching_sources()}

    async def push_frame(
        self, source: str, image_base64: str, mime_type: str,
        captured_at: Optional[float] = None,
    ) -> Dict[str, Any]:
        """外部视觉源推送一帧（base64 图片 → 缓冲统一入口）。

        Raises:
            ValueError: 非法 source 名 / 不支持的类型 / base64 非法。
            OverflowError: 图片为空或超过上限。
        """
        source = source.strip()
        if not source or ".." in source or "/" in source:
            raise ValueError("非法 source 名")
        from agent.vision.framework import is_enabled
        if not is_enabled(f"external:{source}"):
            raise ValueError(f"外部视觉源已停用: {source}")
        if mime_type not in _PUSH_EXTS:
            raise ValueError(f"不支持的图片类型: {mime_type}")
        try:
            data = base64.b64decode(image_base64, validate=True)
        except (binascii.Error, ValueError):
            raise ValueError("image_base64 不是合法的 base64") from None
        if not data or len(data) > _PUSH_MAX_BYTES:
            raise OverflowError("图片为空或超过 20MB 上限")

        from core.path import ConfigPaths
        out_dir = Path(str(ConfigPaths.UPLOAD_DIR)) / "vision"
        out_dir.mkdir(parents=True, exist_ok=True)
        dest = out_dir / f"{int(time.time() * 1000)}_{source}{_PUSH_EXTS[mime_type]}"

        def _write() -> None:
            dest.write_bytes(data)

        await asyncio.to_thread(_write)
        frame, changed = await get_vision_buffer().ingest(
            str(dest), f"external:{source}",
            captured_at=captured_at or time.time(),
        )
        return {
            "ok": True, "path": frame.path, "changed": changed,
            "source": frame.source,
        }


def _face_upload_path(filename: str, content: bytes, *, persistent: bool) -> str:
    """把上传图片落地：persistent 落 uploads/face/（事件引用持久路径），否则临时文件。"""
    suffix = os.path.splitext(filename or "face.jpg")[1].lower()
    if suffix not in _FACE_UPLOAD_EXTS:
        suffix = ".jpg"
    if persistent:
        from core.path import ConfigPaths
        out_dir = Path(str(ConfigPaths.UPLOAD_DIR)) / "face"
        out_dir.mkdir(parents=True, exist_ok=True)
        dest = out_dir / f"{int(time.time() * 1000)}_{os.path.basename(filename or 'face')}"
        dest = dest.with_suffix(suffix)
        dest.write_bytes(content)
        return str(dest)
    fd, tmp_path = tempfile.mkstemp(prefix="face_upload_", suffix=suffix)
    with os.fdopen(fd, "wb") as f:
        f.write(content)
    return tmp_path


class FaceServiceFacade:
    """人脸页签的数据聚合与人脸库管理面（/api/face）。"""

    # ------------------------------------------------------------------
    # 状态总览
    # ------------------------------------------------------------------

    async def status(self, refresh: bool = False) -> Dict[str, Any]:
        """识别引擎状态（地址/可达性/模型/向量维度/设备）+ 库统计 + 生效阈值。"""
        if refresh:
            face_engine.reset_probe_cache()
        health = await face_engine.health()
        stats = await get_face_store().stats()
        return {
            "engine": {
                "configured": face_engine.is_configured(),
                "endpoint": face_engine.endpoint_config(),
                "reachable": await face_engine.probe_available(),
                "health": health.model_dump() if health else None,
            },
            "stats": stats,
            "thresholds": {
                "match": face_matcher.global_threshold(),
                "merge": face_matcher.merge_threshold(),
                "separation": face_matcher.separation_floor(),
            },
        }

    async def stats(self) -> Dict[str, Any]:
        return await get_face_store().stats()

    # ------------------------------------------------------------------
    # 人物身份
    # ------------------------------------------------------------------

    async def list_persons(
        self, status: str = "", keyword: str = "", limit: int = 50, offset: int = 0,
    ) -> Dict[str, Any]:
        return await get_face_store().list_persons(
            status=status, keyword=keyword, limit=min(limit, 200), offset=offset)

    async def person_detail(self, person_id: int) -> Optional[Dict[str, Any]]:
        store = get_face_store()
        person = await store.get_person(person_id)
        if not person:
            return None
        samples = await store.list_samples(person_id)
        recent = await store.list_events(person_id=person_id, limit=5)
        return {
            "person": person,
            "effective_threshold": face_matcher.effective_threshold(person),
            "samples": samples,
            "recent_events": recent["items"],
        }

    async def update_person(self, person_id: int, **fields: Any) -> Optional[Dict[str, Any]]:
        fields = {k: v for k, v in fields.items() if v is not None}
        return await get_face_store().update_person(person_id, **fields)

    async def bind_person(self, person_id: int, entity_scope: str) -> Optional[Dict[str, Any]]:
        return await get_face_store().bind_entity(person_id, entity_scope)

    async def persons_for_entity(self, entity_scope: str) -> List[Dict[str, Any]]:
        return await get_face_store().persons_for_entity(entity_scope)

    async def confirm_person(self, person_id: int, name: str, role: str = "") -> Any:
        return await face_matcher.confirm(get_face_store(), person_id, name, role=role)

    async def delete_person(self, person_id: int) -> Any:
        return await get_face_store().delete_person(person_id)

    async def merge_persons(self, source_id: int, target_id: int) -> Dict[str, Any]:
        return await face_matcher.merge(get_face_store(), source_id, target_id)

    async def refine_person(self, person_id: int) -> Dict[str, Any]:
        """人脸锚重建（样本池重立锚）；样本池为空 raise ValueError（路由转 422）。"""
        return await face_matcher.refine(get_face_store(), person_id)

    async def prune_persons(self, include_with_samples: bool) -> Dict[str, Any]:
        deleted = await get_face_store().prune_pending_persons(
            include_with_samples=include_with_samples)
        return {"pruned": len(deleted), "include_with_samples": include_with_samples,
                "persons": deleted}

    async def consolidate_persons(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        threshold = payload.get("threshold")
        return await face_consolidate_run(
            get_face_store(),
            threshold=float(threshold) if isinstance(threshold, (int, float))
                      and 0.0 < float(threshold) < 1.0 else None,
            dry_run=bool(payload.get("dry_run", True)),
            status="" if payload.get("include_confirmed") else "pending",
            prune_insignificant=bool(payload.get("prune_insignificant", False)),
        )

    async def delete_sample(self, sample_id: int) -> bool:
        return await get_face_store().delete_sample(sample_id)

    # ------------------------------------------------------------------
    # 图片预览（事件/样本缩略图）
    # ------------------------------------------------------------------

    @staticmethod
    def resolve_image(path: str) -> str:
        """校验并返回可服务的图片路径（仅限 uploads 目录内，防路径穿越）。

        Raises:
            ValueError: 路径越界（不在 uploads 目录内）。
            FileNotFoundError: 文件不存在。
        """
        from core.path import ConfigPaths
        upload_root = os.path.realpath(str(ConfigPaths.UPLOAD_DIR))
        target = os.path.realpath(path or "")
        if target != upload_root and not target.startswith(upload_root + os.sep):
            raise ValueError("图片路径越界（仅允许 uploads 目录内）")
        if not os.path.isfile(target):
            raise FileNotFoundError("图片文件不存在")
        return target

    # ------------------------------------------------------------------
    # 出现事件
    # ------------------------------------------------------------------

    async def list_events(
        self, person_id: Optional[int] = None, entity_scope: str = "",
        source: str = "", from_ns: Optional[int] = None, to_ns: Optional[int] = None,
        unread_only: bool = False, limit: int = 20, offset: int = 0,
    ) -> Dict[str, Any]:
        return await get_face_store().list_events(
            person_id=person_id, entity_scope=entity_scope, source=source,
            from_ns=from_ns, to_ns=to_ns, unread_only=unread_only,
            limit=min(limit, 200), offset=offset)

    async def mark_read(self, event_ids: Optional[List[int]], read: bool = True) -> int:
        return await get_face_store().mark_read(event_ids, read=read)

    async def delete_event(self, event_id: int) -> bool:
        return await get_face_store().delete_event(event_id)

    # ------------------------------------------------------------------
    # 识别与注册（上传图片）
    # ------------------------------------------------------------------

    async def identify_image(
        self, filename: str, content: bytes, *, ingest: bool,
    ) -> Dict[str, Any]:
        """上传识别人脸：ingest=true 入库（落 uploads/face/ 持久引用），否则临时预览。

        Raises:
            FaceEngineNotConfigured: 引擎未配置（503）。
            FaceEngineError: 引擎调用失败（502/422）。
        """
        from agent.vision.face.ingest import ingest_image, min_det_score, passes_quality
        path = _face_upload_path(filename, content, persistent=ingest)
        try:
            if not face_engine.is_configured():
                raise FaceEngineNotConfigured(
                    "未配置人脸识别服务地址（组件凭据 face.face_endpoint）")
            if ingest:
                result = await ingest_image(path, "web")
                return {"ingested": True, **result.model_dump()}
            extracted = await face_engine.extract_faces(
                path, min_det_score=min_det_score())
            store = get_face_store()
            faces: List[Dict[str, Any]] = []
            for idx, face in enumerate(extracted.faces):
                entry: Dict[str, Any] = {
                    "index": idx, "bbox": face.bbox,
                    "det_score": round(face.det_score, 4),
                }
                if not face.vector or not passes_quality(face):
                    entry["skipped"] = "质量不足（小脸/低置信度）"
                else:
                    candidates = await face_matcher.match_vector(store, face.vector)
                    entry["best_match"] = candidates[0] if candidates else None
                    entry["candidates"] = candidates[1:3]
                faces.append(entry)
            return {
                "ingested": False, "width": extracted.width, "height": extracted.height,
                "faces_detected": len(extracted.faces), "faces": faces,
            }
        finally:
            if not ingest:
                try:
                    os.unlink(path)
                except OSError:
                    pass

    async def enroll_image(
        self, filename: str, content: bytes, name: str, *,
        face_index: int = 0, role: str = "", notes: str = "", entity_scope: str = "",
    ) -> Dict[str, Any]:
        """上传注册人物人脸：落 uploads/face/ 后检测取指定序号的脸建档。

        Raises:
            FaceEngineNotConfigured / FaceEngineError / ValueError（无合格人脸/越界）。
        """
        from agent.vision.face.ingest import min_det_score
        if not face_engine.is_configured():
            raise FaceEngineNotConfigured(
                "未配置人脸识别服务地址（组件凭据 face.face_endpoint）")
        path = _face_upload_path(filename, content, persistent=True)
        extracted = await face_engine.extract_faces(path, min_det_score=min_det_score())
        qualified = [f for f in extracted.faces if f.vector]
        if not qualified:
            raise ValueError("图片中未检测到合格人脸")
        if not 0 <= face_index < len(qualified):
            raise ValueError(f"face_index {face_index} 越界（检测到 {len(qualified)} 张脸）")
        face = qualified[face_index]
        person = await face_matcher.enroll(
            get_face_store(), name, face.vector, role=role, notes=notes,
            entity_scope=entity_scope, det_score=face.det_score,
            bbox=list(face.bbox), pose=face.pose.model_dump(),
            image_path=path, source="enroll")
        return {
            "person": person, "enrolled_from": path,
            "faces_in_image": len(qualified), "face_index": face_index,
            "det_score": round(face.det_score, 4),
        }

    async def compare_images(
        self, filename_a: str, content_a: bytes, filename_b: str, content_b: bytes,
    ) -> Dict[str, Any]:
        """上传两张图片对比人脸是否同一人（逐脸提取向量余弦配对）。"""
        from agent.vision.face.ingest import min_det_score
        from agent.vision.face.vectors import cosine
        if not face_engine.is_configured():
            raise FaceEngineNotConfigured(
                "未配置人脸识别服务地址（组件凭据 face.face_endpoint）")
        path_a = _face_upload_path(filename_a, content_a, persistent=False)
        path_b = _face_upload_path(filename_b, content_b, persistent=False)
        try:
            extract_a = await face_engine.extract_faces(path_a, min_det_score=min_det_score())
            extract_b = await face_engine.extract_faces(path_b, min_det_score=min_det_score())
            vecs_a = [f.vector for f in extract_a.faces if f.vector]
            vecs_b = [f.vector for f in extract_b.faces if f.vector]
            if not vecs_a or not vecs_b:
                raise ValueError("至少一张图片未检测到合格人脸")
            pairs = [
                {"index_a": ia, "index_b": ib, "similarity": round(cosine(va, vb), 4)}
                for ia, va in enumerate(vecs_a) for ib, vb in enumerate(vecs_b)
            ]
            best = max(pairs, key=lambda p: p["similarity"])
            threshold = face_matcher.global_threshold()
            return {
                "faces_a": len(vecs_a), "faces_b": len(vecs_b),
                "best": best, "threshold": threshold,
                "same_person": best["similarity"] >= threshold,
            }
        finally:
            for p in (path_a, path_b):
                try:
                    os.unlink(p)
                except OSError:
                    pass
