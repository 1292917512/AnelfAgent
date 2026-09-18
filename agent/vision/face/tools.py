"""人脸核心 AI 工具面：人物身份管理、人脸识别、出现事件检索与库治理。

设计原则（AI 视角，与音频工具面同一纪律）：
- person 参数统一接受 数字id / person_key / 姓名（内部模糊解析，重名返回候选供追问）
- 时间参数接受自然日期串（'2026-08-01' / '2026-08-01 14:00' / epoch 秒）
- 查询类工具按需激活（tags=core）且只读并发安全；face_identify 兼
  media:image 标签（图片入站即随媒体工具族召回）
- 依赖引擎的工具声明 check_fn（未配置服务地址时不出现在 schema）
- 返回统一 JSON 字符串；错误用 tool_error/error_from_exception 归因
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional, Tuple

from core.config import get_config_bool
from core.tool_errors import ErrorCause
from entities._sdk import deferred_tool, error_from_exception, tool_error

from . import consolidate, engine, matcher
from .store import get_face_store
from .vectors import cosine

_group = "vision"
_LOG_TAG = "人脸"


def _gate() -> Optional[str]:
    """核心开关门控：face_ai_enabled 关闭时拒绝 AI 调用。"""
    if not get_config_bool("face_ai_enabled", True):
        return tool_error(
            "人脸核心能力已停用", cause=ErrorCause.STATE, retryable=False,
            hint="请在配置中心 vision/face 组开启 face_ai_enabled")
    return None


def _dump(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False)


async def _resolve_person(ref: str) -> Tuple[Optional[Dict[str, Any]], str]:
    """解析人物引用（id / person_key / 姓名）。返回 (person, error_json)。"""
    ref = (ref or "").strip()
    if not ref:
        return None, tool_error("person 参数不能为空", cause=ErrorCause.PARAM, retryable=False)
    store = get_face_store()
    matches = await store.find_persons(ref)
    if not matches:
        return None, tool_error(
            f"未找到人物: {ref}", cause=ErrorCause.NOT_FOUND, retryable=False,
            hint="可用 face_list 查看全部人物")
    exact = [m for m in matches if m["name"] == ref or m["person_key"] == ref
             or str(m["id"]) == ref]
    if len(matches) > 1 and len(exact) != 1:
        candidates = [
            {"id": m["id"], "person_key": m["person_key"], "name": m["name"],
             "role": m["role"], "status": m["status"]}
            for m in matches[:5]
        ]
        return None, tool_error(
            f"'{ref}' 匹配到多个人物，请用 id 或 person_key 指定",
            cause=ErrorCause.PARAM, retryable=False, candidates=candidates)
    return (exact[0] if exact else matches[0]), ""


def _resolve_image_path(path: str) -> str:
    """解析图片路径：绝对路径直用，相对路径走 workspace 沙箱解析。"""
    path = (path or "").strip()
    if not path:
        raise ValueError("image_path 不能为空")
    if os.path.isabs(path):
        return path
    from agent.approval.policy import workspace_paths_port
    if workspace_paths_port.bound:
        return workspace_paths_port.get().resolve(path)
    raise ValueError("相对路径需要 workspace 解析（请传绝对路径）")


def _engine_unavailable() -> str:
    return tool_error(
        "人脸识别服务未配置或不可用", cause=ErrorCause.CONFIG, retryable=False,
        hint="请在视觉页 → 组件凭据配置 face.face_endpoint（如 http://gpu-host:10097）")


# ------------------------------------------------------------------
# 查询类
# ------------------------------------------------------------------


@deferred_tool(group=_group, tags=["core"], concurrency_safe=True)
async def face_list(status: str = "", keyword: str = "", limit: int = 20) -> str:
    """列出人脸库中的人物（含样本数/实体绑定/最近出现），支持状态与关键字过滤。

    Args:
        status: 状态过滤，'confirmed'（已确认）或 'pending'（待确认），空为全部
        keyword: 姓名/角色/编号模糊过滤，空为全部
        limit: 返回数量上限（默认 20）
    """
    if (gate := _gate()):
        return gate
    try:
        store = get_face_store()
        result = await store.list_persons(status=status, keyword=keyword, limit=limit)
        return _dump(result)
    except Exception as e:
        return error_from_exception(e, action="列出人物")


@deferred_tool(group=_group, tags=["core"], concurrency_safe=True)
async def face_get(person: str) -> str:
    """查看人物档案详情：样本池、有效阈值、实体绑定、统计信息与近期出现事件。

    Args:
        person: 人物引用（数字id / person_key / 姓名）
    """
    if (gate := _gate()):
        return gate
    try:
        target, err = await _resolve_person(person)
        if err:
            return err
        assert target is not None
        store = get_face_store()
        person_id = int(target["id"])
        samples = await store.list_samples(person_id)
        events = await store.list_events(person_id=person_id, limit=5)
        return _dump({
            "person": {**target, "effective_threshold": matcher.effective_threshold(target)},
            "samples": [{k: v for k, v in s.items() if k != "dims"} for s in samples],
            "recent_events": events["items"],
        })
    except Exception as e:
        return error_from_exception(e, action=f"查看人物 [{person}]")


@deferred_tool(group=_group, tags=["core", "face_scope"], concurrency_safe=True)
async def face_events(
    person: str = "", from_time: str = "", to_time: str = "",
    unread_only: bool = False, limit: int = 10,
) -> str:
    """查询画面出现事件时间线（在哪见过谁）：按人物/时间范围/未读过滤。

    Args:
        person: 人物引用（id/key/姓名），空为全部人物
        from_time: 起始时间（'2026-08-01' / 'YYYY-MM-DD HH:MM' / epoch 秒），空不限
        to_time: 截止时间，格式同上，空不限
        unread_only: 仅未读事件（True 时返回后自动标记已读）
        limit: 返回数量上限（默认 10）
    """
    if (gate := _gate()):
        return gate
    try:
        from agent.audio.store import parse_time_ns
        store = get_face_store()
        person_id: Optional[int] = None
        if person.strip():
            target, err = await _resolve_person(person)
            if err:
                return err
            assert target is not None
            person_id = int(target["id"])
        from_ns = parse_time_ns(from_time)
        to_ns = parse_time_ns(to_time)
        result = await store.list_events(
            person_id=person_id, from_ns=from_ns, to_ns=to_ns,
            unread_only=unread_only, limit=limit)
        if unread_only and result["items"]:
            await store.mark_read([e["id"] for e in result["items"]])
        return _dump(result)
    except Exception as e:
        return error_from_exception(e, action="查询出现事件")


@deferred_tool(group=_group, tags=["core"], concurrency_safe=True)
async def face_stats() -> str:
    """人脸库总览：人物/样本/事件统计 + 识别引擎状态（地址/可达性/模型/向量维度）。"""
    if (gate := _gate()):
        return gate
    try:
        store = get_face_store()
        stats = await store.stats()
        health = await engine.health()
        return _dump({
            "stats": stats,
            "engine": {
                "configured": engine.is_configured(),
                "endpoint": engine.endpoint_config(),
                "reachable": await engine.probe_available(),
                "health": health.model_dump() if health else None,
            },
            "thresholds": {
                "match": matcher.global_threshold(),
                "merge": matcher.merge_threshold(),
                "separation": matcher.separation_floor(),
            },
        })
    except Exception as e:
        return error_from_exception(e, action="人脸库统计")


# ------------------------------------------------------------------
# 识别与注册
# ------------------------------------------------------------------


@deferred_tool(group=_group, tags=["core", "media:image"], check_fn=engine.is_configured)
async def face_identify(image_path: str, ingest: bool = False) -> str:
    """识别图片中的人脸：检测+提取+匹配，逐脸返回 Top 候选与新人标记。

    Args:
        image_path: 图片文件路径（绝对路径或 workspace 相对路径）
        ingest: 识别结果是否同时入库（True 时出现事件进入时间线、命中累积样本、
            新人自动建临时档案）
    """
    if (gate := _gate()):
        return gate
    try:
        resolved = _resolve_image_path(image_path)
        if not os.path.isfile(resolved):
            return tool_error(f"图片文件不存在: {resolved}",
                              cause=ErrorCause.PARAM, retryable=False)
        if ingest:
            from .ingest import ingest_image
            result = await ingest_image(resolved, "manual")
            return _dump(result.model_dump())

        from .ingest import min_det_score, passes_quality
        extracted = await engine.extract_faces(resolved, min_det_score=min_det_score())
        store = get_face_store()
        faces: List[Dict[str, Any]] = []
        for idx, face in enumerate(extracted.faces):
            entry: Dict[str, Any] = {
                "index": idx, "bbox": face.bbox,
                "det_score": round(face.det_score, 4),
            }
            if not face.vector or not passes_quality(face):
                entry["skipped"] = "质量不足（小脸/低置信度），不参与匹配"
                faces.append(entry)
                continue
            candidates = await matcher.match_vector(store, face.vector)
            best = candidates[0] if candidates else None
            entry["best_match"] = best
            entry["candidates"] = candidates[1:3]
            faces.append(entry)
        return _dump({
            "image_path": resolved, "ingested": False,
            "width": extracted.width, "height": extracted.height,
            "faces_detected": len(extracted.faces), "faces": faces,
            "hint": "ingest=True 可把本次识别入库（事件时间线+样本累积）",
        })
    except engine.FaceEngineNotConfigured:
        return _engine_unavailable()
    except engine.FaceEngineError as e:
        return tool_error(str(e), cause=ErrorCause.NETWORK if e.retryable
                          else ErrorCause.PARAM, retryable=e.retryable)
    except Exception as e:
        return error_from_exception(e, action=f"识别人脸 [{image_path}]")


@deferred_tool(group=_group, tags=["core", "media:image"], check_fn=engine.is_configured)
async def face_enroll(
    image_path: str, name: str, face_index: int = 0,
    role: str = "", notes: str = "", entity_scope: str = "",
) -> str:
    """从图片注册人物人脸：检测后取指定序号的脸建档（同名已确认档案则累积样本）。

    Args:
        image_path: 图片文件路径（绝对路径或 workspace 相对路径）
        name: 人物姓名（必填；同名已确认档案直接累积样本，一人一档案）
        face_index: 取第几张脸（按检测置信度降序，默认 0=最显著的脸）
        role: 角色备注（如 '主人'/'同事'）
        notes: 备注信息
        entity_scope: 注册即绑定的实体 scope（如 'user:qq:456'），空为不绑定
    """
    if (gate := _gate()):
        return gate
    if not name.strip():
        return tool_error("name 不能为空", cause=ErrorCause.PARAM, retryable=False)
    try:
        resolved = _resolve_image_path(image_path)
        if not os.path.isfile(resolved):
            return tool_error(f"图片文件不存在: {resolved}",
                              cause=ErrorCause.PARAM, retryable=False)
        from .ingest import min_det_score, persist_image
        # 样本引用持久路径（防源文件清理后缩略图 404）
        resolved = persist_image(resolved)
        extracted = await engine.extract_faces(resolved, min_det_score=min_det_score())
        qualified = [f for f in extracted.faces if f.vector]
        if not qualified:
            return tool_error("图片中未检测到合格人脸", cause=ErrorCause.STATE,
                              retryable=False,
                              hint="换一张正脸、光线充足、分辨率足够的图片")
        if not 0 <= face_index < len(qualified):
            return tool_error(
                f"face_index {face_index} 越界（检测到 {len(qualified)} 张脸）",
                cause=ErrorCause.PARAM, retryable=False)
        face = qualified[face_index]
        store = get_face_store()
        try:
            result = await matcher.enroll(
                store, name.strip(), face.vector, role=role, notes=notes,
                entity_scope=entity_scope, det_score=face.det_score,
                bbox=list(face.bbox), pose=face.pose.model_dump(),
                image_path=resolved, source="enroll")
        except ValueError as e:
            return tool_error(str(e), cause=ErrorCause.PARAM, retryable=False)
        return _dump({
            "person": result,
            "enrolled_from": resolved,
            "faces_in_image": len(qualified),
            "face_index": face_index,
            "det_score": round(face.det_score, 4),
        })
    except engine.FaceEngineNotConfigured:
        return _engine_unavailable()
    except engine.FaceEngineError as e:
        return tool_error(str(e), cause=ErrorCause.NETWORK if e.retryable
                          else ErrorCause.PARAM, retryable=e.retryable)
    except Exception as e:
        return error_from_exception(e, action=f"注册人脸 [{name}]")


@deferred_tool(group=_group, tags=["core"], concurrency_safe=True,
               check_fn=engine.is_configured)
async def face_compare_images(image_path_a: str, image_path_b: str) -> str:
    """对比两张图片中的人脸是否同一人：逐脸提取向量做余弦配对。

    Args:
        image_path_a: 第一张图片路径
        image_path_b: 第二张图片路径
    """
    if (gate := _gate()):
        return gate
    try:
        from .ingest import min_det_score
        path_a = _resolve_image_path(image_path_a)
        path_b = _resolve_image_path(image_path_b)
        extract_a = await engine.extract_faces(path_a, min_det_score=min_det_score())
        extract_b = await engine.extract_faces(path_b, min_det_score=min_det_score())
        vecs_a = [f.vector for f in extract_a.faces if f.vector]
        vecs_b = [f.vector for f in extract_b.faces if f.vector]
        if not vecs_a or not vecs_b:
            return tool_error("至少一张图片未检测到合格人脸",
                              cause=ErrorCause.STATE, retryable=False)
        pairs = [
            {"index_a": ia, "index_b": ib, "similarity": round(cosine(va, vb), 4)}
            for ia, va in enumerate(vecs_a) for ib, vb in enumerate(vecs_b)
        ]
        best = max(pairs, key=lambda p: p["similarity"])
        threshold = matcher.global_threshold()
        return _dump({
            "faces_a": len(vecs_a), "faces_b": len(vecs_b),
            "best": best, "threshold": threshold,
            "same_person": best["similarity"] >= threshold,
            "verdict": (f"最佳配对相似度 {best['similarity']}（判定线 {threshold}）："
                        + ("倾向同一人" if best["similarity"] >= threshold else "倾向不同人")),
        })
    except engine.FaceEngineNotConfigured:
        return _engine_unavailable()
    except engine.FaceEngineError as e:
        return tool_error(str(e), cause=ErrorCause.NETWORK if e.retryable
                          else ErrorCause.PARAM, retryable=e.retryable)
    except Exception as e:
        return error_from_exception(e, action="图片人脸对比")


# ------------------------------------------------------------------
# 管理类
# ------------------------------------------------------------------


@deferred_tool(group=_group, tags=["core"])
async def face_bind(person: str, entity_scope: str = "") -> str:
    """人脸身份 ↔ 实体画像绑定：绑定后画面识别命中即自动召回该实体的画像与记忆。

    Args:
        person: 人物引用（id/key/姓名）
        entity_scope: 实体 scope（如 'user:webui:123' / 'group:qq:456' / 'agent:self'），
            空串解除绑定
    """
    if (gate := _gate()):
        return gate
    try:
        target, err = await _resolve_person(person)
        if err:
            return err
        assert target is not None
        store = get_face_store()
        try:
            updated = await store.bind_entity(int(target["id"]), entity_scope)
        except ValueError as e:
            return tool_error(str(e), cause=ErrorCause.PARAM, retryable=False)
        return _dump({"person": updated, "bound": bool(entity_scope.strip()),
                      "hint": "绑定后该人物出现在画面中时，其实体画像与记忆自动参与召回"})
    except Exception as e:
        return error_from_exception(e, action=f"绑定实体 [{person}]")


@deferred_tool(group=_group, tags=["core"])
async def face_update(
    person: str, name: str = "", role: str = "", notes: str = "",
    confirm: bool = False, threshold: float = -1.0,
    entity_scope: Optional[str] = None,
) -> str:
    """更新人物档案：改名/角色/备注/确认临时人物/独立匹配阈值/实体绑定（仅更新传入的字段）。

    Args:
        person: 人物引用（id/key/姓名）
        name: 新姓名（空为不改；确认临时人物时必填）
        role: 角色备注（空为不改）
        notes: 备注（空为不改）
        confirm: True 时把 pending 临时人物转为 confirmed（需 name）
        threshold: 独立匹配阈值（覆盖全局值；-1 为不改，0 为清除恢复全局）
        entity_scope: 绑定的实体 scope（如 'user:qq:456'，命中即召回该实体画像记忆）；
            空串解除绑定；不传（缺省）保持现状不改
    """
    if (gate := _gate()):
        return gate
    try:
        target, err = await _resolve_person(person)
        if err:
            return err
        assert target is not None
        store = get_face_store()
        fields: Dict[str, Any] = {}
        if name.strip():
            fields["name"] = name.strip()
        if role.strip():
            fields["role"] = role.strip()
        if notes.strip():
            fields["notes"] = notes.strip()
        if threshold >= 0:
            fields["threshold"] = threshold or None
        if confirm:
            if not fields.get("name") and not target["name"]:
                return tool_error("确认临时人物需要 name（赋正式姓名）",
                                  cause=ErrorCause.PARAM, retryable=False)
            fields["status"] = "confirmed"
            fields.setdefault("name", target["name"])
        if not fields and entity_scope is None:
            return tool_error("没有任何要更新的字段", cause=ErrorCause.PARAM, retryable=False)
        updated = target
        if fields:
            updated = await store.update_person(int(target["id"]), **fields) or target
        applied = sorted(fields)
        if entity_scope is not None:
            try:
                updated = await store.bind_entity(int(target["id"]), entity_scope) or target
            except ValueError as e:
                return tool_error(str(e), cause=ErrorCause.PARAM, retryable=False)
            applied.append("entity_scope")
        return _dump({"person": updated, "updated_fields": applied})
    except Exception as e:
        return error_from_exception(e, action=f"更新人物 [{person}]")


@deferred_tool(group=_group, tags=["core"], concurrency_safe=True)
async def face_compare(person_a: str, person_b: str) -> str:
    """精确对比两个人物档案的人脸：锚余弦 + 样本最佳配对 + 合并判读。

    Args:
        person_a: 人物 A 引用（id/key/姓名）
        person_b: 人物 B 引用（id/key/姓名）
    """
    if (gate := _gate()):
        return gate
    try:
        target_a, err = await _resolve_person(person_a)
        if err:
            return err
        target_b, err = await _resolve_person(person_b)
        if err:
            return err
        assert target_a is not None and target_b is not None
        store = get_face_store()
        result = await matcher.compare(store, int(target_a["id"]), int(target_b["id"]))
        return _dump(result)
    except ValueError as e:
        return tool_error(str(e), cause=ErrorCause.PARAM, retryable=False)
    except Exception as e:
        return error_from_exception(e, action="人物人脸对比")


@deferred_tool(group=_group, tags=["core"])
async def face_merge(source: str, target: str) -> str:
    """人物身份合并：source 并入 target（同一人被分裂成多档案时的归一路径）。

    样本池整体迁移、人脸锚按权重精确合成、统计量累加，source 档案删除。

    Args:
        source: 被合并的人物引用（id/key/姓名）
        target: 合并目标人物引用（保留的档案）
    """
    if (gate := _gate()):
        return gate
    try:
        src, err = await _resolve_person(source)
        if err:
            return err
        dst, err = await _resolve_person(target)
        if err:
            return err
        assert src is not None and dst is not None
        store = get_face_store()
        result = await matcher.merge(store, int(src["id"]), int(dst["id"]))
        return _dump(result)
    except ValueError as e:
        return tool_error(str(e), cause=ErrorCause.PARAM, retryable=False)
    except Exception as e:
        return error_from_exception(e, action=f"合并人物 [{source}]→[{target}]")


@deferred_tool(group=_group, tags=["core"])
async def face_refine(person: str) -> str:
    """人脸锚重建：以当前样本池重立锚（剔除坏样本后复位、或锚被误匹配带偏时用）。

    Args:
        person: 人物引用（id/key/姓名）
    """
    if (gate := _gate()):
        return gate
    try:
        target, err = await _resolve_person(person)
        if err:
            return err
        assert target is not None
        store = get_face_store()
        result = await matcher.refine(store, int(target["id"]))
        return _dump(result)
    except ValueError as e:
        return tool_error(str(e), cause=ErrorCause.PARAM, retryable=False)
    except Exception as e:
        return error_from_exception(e, action=f"重建人脸锚 [{person}]")


@deferred_tool(group=_group, tags=["core"])
async def face_delete(person: str) -> str:
    """删除人物档案（级联删除样本池；出现事件保留为客观画面历史）。

    Args:
        person: 人物引用（id/key/姓名）
    """
    if (gate := _gate()):
        return gate
    try:
        target, err = await _resolve_person(person)
        if err:
            return err
        assert target is not None
        store = get_face_store()
        deleted = await store.delete_person(int(target["id"]))
        return _dump({"deleted": deleted})
    except Exception as e:
        return error_from_exception(e, action=f"删除人物 [{person}]")


@deferred_tool(group=_group, tags=["core"])
async def face_prune_pending(include_with_samples: bool = False) -> str:
    """批量剔除临时（pending）人物档案（群聊路人脸的定期清理）。

    Args:
        include_with_samples: False 只清理无样本的空壳档案（安全默认）；
            True 剔除全部 pending（级联删样本）
    """
    if (gate := _gate()):
        return gate
    try:
        store = get_face_store()
        deleted = await store.prune_pending_persons(
            include_with_samples=include_with_samples)
        return _dump({"deleted_count": len(deleted), "deleted": deleted})
    except Exception as e:
        return error_from_exception(e, action="清理临时人物")


@deferred_tool(group=_group, tags=["core"])
async def face_consolidate(
    dry_run: bool = True, prune_insignificant: bool = False,
    threshold: float = -1.0,
) -> str:
    """人脸库离线整理：锚相似度聚类找出同一人被分裂的档案 + 低价值路人清理。

    Args:
        dry_run: True 只返回分簇预览（默认），False 正式执行合并
        prune_insignificant: 执行时是否一并剔除低价值临时人物（命中少的路人）
        threshold: 聚类合并阈值（-1 用全局 face_merge_threshold）
    """
    if (gate := _gate()):
        return gate
    try:
        store = get_face_store()
        result = await consolidate.consolidate(
            store,
            threshold=threshold if threshold >= 0 else None,
            dry_run=dry_run, prune_insignificant=prune_insignificant)
        return _dump(result)
    except Exception as e:
        return error_from_exception(e, action="人脸库整理")
