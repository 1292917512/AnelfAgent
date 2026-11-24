"""上下文快照捕获器 — 捕获 LLM 调用的完整上下文并持久化。

布防后在下一次 LLM 调用前捕获完整 messages + tools（含 _layer 分类标签），
捕获后自动解除布防并保存到 logs/context_snapshots/ 目录。
调用结束后按 call_id 回填同一次响应的用量；连续记录只追加终态。
敏感结果在观测副本中隐藏原文，字符数与变更哈希仍按真实输入计算。

未布防时 try_capture 仅做一次 bool 检查，零开销。
"""

from __future__ import annotations

import asyncio
import json
import os
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Tuple, TypeVar

from agent.context.cache_stats import cache_usage_record
from agent.context.pipeline import get_layer_meta, list_layer_metas
from agent.llm.types import UsageInfo
from core.file_utils import atomic_write_text
from core.log import log
from core.tool_results import redact_tool_messages

_SNAPSHOT_DIR = os.path.join("logs", "context_snapshots")
_STORE_LOCK = threading.RLock()

# 变更对比基线的族数量上限（每族 = scope × kind；各族按层存若干哈希条目，
# 条目总量上限约为族数 × 层数）
_MAX_DIFF_FAMILIES = 64


# ======================================================================
# 分类定义（层标签/顺序的单一数据源在 context.pipeline 注册中心）
# ======================================================================

def _layer_label(layer: str) -> str:
    meta = get_layer_meta(layer)
    return meta.label if meta else layer


def _layer_order() -> List[str]:
    return [m.layer for m in list_layer_metas()]


# ======================================================================
# 捕获器
# ======================================================================


@dataclass
class SnapshotCapture:
    """单次调用持有的观测副本，不注册全局待完成请求，也不进入模型上下文。"""

    snapshot: Dict[str, Any]
    filename: str
    continuous: bool


class ContextSnapshot:
    """上下文快照捕获器（单例）。

    两种捕获模式：
    - 一次性布防（arm）：下一次 LLM 调用捕获后自动解除
    - 连续捕获（continuous）：开启后每次 LLM 调用都捕获并追加紧凑记录
      （records.jsonl，供外部调试工具轮询），关闭即零开销
    """

    def __init__(self) -> None:
        self._armed: bool = False
        self._continuous: bool = False
        self._snapshot: Optional[Dict[str, Any]] = None
        self._lock = asyncio.Lock()
        # 上一次快照的 section 哈希（(scope, kind, layer) → sha1 前缀），用于
        # 逐 section 变更对比。基线按前缀族（scope × 调用用途）分键——
        # reply 与 reflect/任务的前缀构成不同（精简 stable、不同 tools 数组），
        # 跨族对比会把"族间差异"误标为"层漂移"（每行都显示 stable 变更），
        # 与 PrefixGuard 的 (scope, kind) 分键同一分类学
        self._last_section_hashes: Dict[Tuple[str, str, str], str] = {}
        # 上一次快照的逐消息哈希（(scope, kind, layer) → [sha1]），用于区块内
        # "已缓存前缀 / 本轮新增"切分（追加式层的新增部分与既有前缀分开呈现）
        self._last_msg_hashes: Dict[Tuple[str, str, str], List[str]] = {}

    @property
    def armed(self) -> bool:
        return self._armed

    @property
    def continuous(self) -> bool:
        return self._continuous

    @property
    def has_snapshot(self) -> bool:
        return self._snapshot is not None

    async def arm(self) -> None:
        """布防：等待下一次 LLM 调用时捕获。"""
        async with self._lock:
            self._armed = True
            log("上下文快照已布防", "DEBUG", tag="快照")

    async def disarm(self) -> None:
        """取消布防。"""
        async with self._lock:
            self._armed = False
            log("上下文快照已取消布防", "DEBUG", tag="快照")

    def set_continuous(self, enabled: bool) -> None:
        """开关连续捕获模式（每次 LLM 调用都捕获快照并追加记录）。"""
        self._continuous = enabled
        log(f"上下文快照连续捕获: {'开启' if enabled else '关闭'}", "DEBUG", tag="快照")

    async def try_capture(
            self,
            messages: List[Dict],
            tools: Optional[List[Dict]],
            model: str,
            *,
            kind: str = "",
            scope: str = "",
            prefix_drift: Optional[Dict[str, Any]] = None,
            legal_break: Optional[str] = None,
            call_id: str = "",
    ) -> Optional[SnapshotCapture]:
        """尝试捕获（在 ModelInvoker.invoke 中 normalize 前调用）。

        未布防且未开启连续捕获时立即返回 None。
        返回的句柄由调用方持有，并在响应完成或异常时交给 complete 回填。
        kind 为调用用途（reply/reflect…），随快照记录供列表按用途解读命中率。
        scope 为会话 scope（含 reflect 的一次性 scope），与 kind 共同构成
        变更对比的前缀族键——跨族比较会把族间差异误标为层漂移。
        prefix_drift 为 PrefixGuard 的前缀断裂归因（None=前缀稳定/无基线），
        随快照落盘供缓存命中率下跌归因。
        legal_break 为合法断裂原因（fold/compress，None=不在合法断裂窗口），
        供命中率列表与供应商侧波动区分。
        """
        if not self._armed and not self._continuous:
            return None

        async with self._lock:
            oneshot = self._armed
            if not oneshot and not self._continuous:
                return None

            sections = self._categorize(messages, (scope, kind))
            tool_names = [
                (t.get("function", {}) or {}).get("name", "")
                for t in (tools or [])
            ]

            # 获取模型上下文窗口
            model_context_window = self._get_model_context_window()

            # 估算总 token 数（粗略：字符数 / 4）
            total_chars = sum(
                len(str(m.get("content", "")))
                for m in messages
            )
            estimated_tokens = total_chars // 4

            self._snapshot = {
                "call_id": call_id or uuid.uuid4().hex,
                "captured_at": time.time(),
                "model": model,
                "kind": kind,
                "scope": scope,
                "model_context_window": model_context_window,
                "estimated_tokens": estimated_tokens,
                "message_count": len(messages),
                "tool_count": len(tools) if tools else 0,
                "tool_names": tool_names,
                "tools": tools or [],
                "sections": sections,
                "prefix_break": self._compute_prefix_break(sections),
                "cache": self._build_cache_block(sections),
                "prefix_drift": prefix_drift,
                "legal_break": legal_break,
            }
            self._armed = False

            # 持久化（同步文件写放工作线程，避免持锁阻塞事件循环）
            capture = SnapshotCapture(self._snapshot, "", self._continuous)
            capture.filename = await asyncio.to_thread(self._save, capture.snapshot)

            log(
                f"上下文快照已捕获: {len(messages)} msgs, "
                f"{len(tool_names)} tools, ~{estimated_tokens} tokens"
                f"{f', 已保存 {capture.filename}' if capture.filename else ''}",
                tag="快照",
            )
            return capture

    async def complete(
        self,
        capture: Optional[SnapshotCapture],
        *,
        model: str,
        usage: Optional[UsageInfo] = None,
        status: Literal["completed", "failed", "cancelled"] = "completed",
    ) -> None:
        """回填本次调用的终态；缺失或不可观测的 usage 不冒充零命中。"""
        if capture is None:
            return
        try:
            async with self._lock:
                snapshot = capture.snapshot
                cache = snapshot["cache"]
                if cache["status"] != "pending":
                    return
                call = cache_usage_record(usage, kind=snapshot["kind"], model=model) if usage else None
                if call is not None:
                    call["call_id"] = snapshot["call_id"]
                    call["scope"] = snapshot["scope"]
                snapshot["model"] = model
                snapshot["completed_at"] = time.time()
                cache.update(status=status, call=call)
                await asyncio.to_thread(self._finish_capture, capture)
        except Exception as exc:
            log(f"快照用量回填失败: {exc}", "DEBUG", tag="快照")

    @classmethod
    def _finish_capture(cls, capture: SnapshotCapture) -> None:
        """原子替换尚存在的捕获文件；删除过的快照不因晚到响应重新出现。"""
        with _STORE_LOCK:
            path = os.path.join(_SNAPSHOT_DIR, capture.filename)
            if not capture.filename or not os.path.isfile(path):
                return
            if cls._save(capture.snapshot, filename=capture.filename) and capture.continuous:
                cls._append_record(capture.snapshot, capture.filename)

    def get(self) -> Optional[Dict[str, Any]]:
        """获取当前内存快照。"""
        return self._snapshot

    def clear(self) -> None:
        """清除内存快照 + 解除布防 + 重置 section 变更基线。"""
        self._armed = False
        self._snapshot = None
        self._last_section_hashes = {}
        self._last_msg_hashes = {}

    def get_status(self) -> Dict[str, Any]:
        """返回当前状态（API 用）。"""
        return {
            "armed": self._armed,
            "continuous": self._continuous,
            "has_snapshot": self._snapshot is not None,
            "captured_at": self._snapshot.get("captured_at") if self._snapshot else None,
            "model": self._snapshot.get("model") if self._snapshot else None,
            "model_context_window": self._snapshot.get("model_context_window") if self._snapshot else None,
            "estimated_tokens": self._snapshot.get("estimated_tokens") if self._snapshot else None,
            "message_count": self._snapshot.get("message_count") if self._snapshot else None,
            "tool_count": self._snapshot.get("tool_count") if self._snapshot else None,
        }

    # ------------------------------------------------------------------
    # 连续捕获记录（紧凑 JSONL，供外部调试工具轮询）
    # ------------------------------------------------------------------

    _RECORDS_FILE = "records.jsonl"

    @classmethod
    def _records_path(cls) -> str:
        return os.path.join(_SNAPSHOT_DIR, cls._RECORDS_FILE)

    @classmethod
    def _append_record(cls, snapshot: Dict[str, Any], filename: str) -> None:
        """追加一条紧凑捕获记录（不含消息正文，仅分层统计 + 缓存观测）。"""
        try:
            os.makedirs(_SNAPSHOT_DIR, exist_ok=True)
            record = {
                "call_id": snapshot.get("call_id"),
                "captured_at": snapshot.get("captured_at"),
                "completed_at": snapshot.get("completed_at"),
                "file": filename,
                "model": snapshot.get("model"),
                "kind": snapshot.get("kind"),
                "scope": snapshot.get("scope"),
                "estimated_tokens": snapshot.get("estimated_tokens"),
                "message_count": snapshot.get("message_count"),
                "tool_count": snapshot.get("tool_count"),
                "sections": [
                    {
                        "layer": s["layer"],
                        "count": s["count"],
                        "chars": s["chars"],
                        "estimated_tokens": s["estimated_tokens"],
                        "hash": s.get("hash"),
                        "changed": s.get("changed"),
                        "stable_count": s.get("stable_count"),
                        "new_count": s.get("new_count"),
                    }
                    for s in snapshot.get("sections", [])
                ],
                "prefix_break": snapshot.get("prefix_break"),
                "cache": snapshot.get("cache"),
                "prefix_drift": snapshot.get("prefix_drift"),
                "legal_break": snapshot.get("legal_break"),
            }
            with open(cls._records_path(), "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception as exc:
            log(f"快照记录追加失败: {exc}", "DEBUG", tag="快照")

    @classmethod
    def list_records(cls, limit: int = 100) -> List[Dict[str, Any]]:
        """读取最近的连续捕获记录（JSONL 尾部，按时间正序返回）。"""
        path = cls._records_path()
        if not os.path.isfile(path):
            return []
        try:
            with open(path, "r", encoding="utf-8") as f:
                lines = f.readlines()
            records: List[Dict[str, Any]] = []
            for line in lines[-max(1, limit):]:
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except ValueError:
                    continue
            return records
        except Exception:
            return []

    # ------------------------------------------------------------------
    # 持久化
    # ------------------------------------------------------------------

    @staticmethod
    def _get_model_context_window() -> int:
        """获取当前激活模型的上下文窗口大小。"""
        try:
            from agent.llm.llm_client import LLMClient
            from agent.runtime.singleton import require_runtime
            rt = require_runtime()
            llm = rt.llm
            if isinstance(llm, LLMClient):
                info = LLMClient.get_model_info(llm.config.litellm_model)
                ctx = info.get("max_input_tokens") or info.get("max_tokens") or 0
                if not ctx:
                    ctx = llm.config.context_window or 0
                return ctx
        except Exception:
            log("_get_model_context_window 异常已忽略", "DEBUG")
        return 0

    @staticmethod
    def _save(snapshot: Dict[str, Any], *, filename: str = "") -> str:
        """保存快照到 JSON 文件，返回文件名。"""
        try:
            ts = time.strftime("%Y%m%d_%H%M%S", time.localtime(snapshot["captured_at"]))
            filename = filename or f"snapshot_{ts}_{snapshot['call_id']}.json"
            path = Path(_SNAPSHOT_DIR) / filename
            # 持久化时不保存完整 tools schema（体积大），只保存名称
            save_data = {**snapshot}
            save_data["tools"] = snapshot.get("tool_names", [])
            with _STORE_LOCK:
                atomic_write_text(path, json.dumps(save_data, ensure_ascii=False, indent=2, default=str))
            return filename
        except Exception as exc:
            log(f"快照保存失败: {exc}", "DEBUG", tag="快照")
            return ""

    @staticmethod
    def list_snapshots() -> List[Dict[str, Any]]:
        """列出所有已保存的快照（摘要信息 + 缓存命中简况，供列表直读与外部分析）。"""
        result: List[Dict[str, Any]] = []
        if not os.path.isdir(_SNAPSHOT_DIR):
            return result
        for fname in sorted(os.listdir(_SNAPSHOT_DIR), reverse=True):
            if not fname.endswith(".json"):
                continue
            path = os.path.join(_SNAPSHOT_DIR, fname)
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                cache = data.get("cache") or {}
                # 仅展示按调用标识绑定的响应。历史文件的 last_call 无法证明归属。
                call = cache.get("call") or {}
                linked = bool(data.get("call_id")) and call.get("call_id") == data["call_id"]
                measurable = linked and not call.get("unobservable")
                # 前缀字节是否稳定：除每轮必变的工具链/实时注入/执行态外
                # 所有 section 未变（None=无基线无法判定）。不包含工具 schema
                # 或供应商最终请求，不能由此推断命中下降的原因。
                tail_layers = {"tool_chain", "provider", "exec_context"}
                prefix_flags = [
                    s.get("changed") for s in data.get("sections", [])
                    if s.get("layer") not in tail_layers
                ]
                prefix_stable: Optional[bool] = None
                if prefix_flags:
                    if all(f is False for f in prefix_flags):
                        prefix_stable = True
                    elif any(f is True for f in prefix_flags):
                        prefix_stable = False
                result.append({
                    "filename": fname,
                    "call_id": data.get("call_id"),
                    "scope": data.get("scope", ""),
                    "cache_status": cache.get("status", "unlinked"),
                    "cache_unobservable": bool(linked and call.get("unobservable")),
                    "captured_at": data.get("captured_at", 0),
                    "model": data.get("model", ""),
                    "kind": data.get("kind", ""),
                    "prefix_stable": prefix_stable,
                    "legal_break": data.get("legal_break"),
                    "model_context_window": data.get("model_context_window", 0),
                    "estimated_tokens": data.get("estimated_tokens", 0),
                    "message_count": data.get("message_count", 0),
                    "tool_count": data.get("tool_count", 0),
                    "cache_hit_rate": call.get("cache_hit_rate") if measurable else None,
                    "cache_read_input_tokens": call.get("cache_read_input_tokens") if measurable else None,
                    "cache_creation_input_tokens": call.get("cache_creation_input_tokens") if measurable else None,
                    "estimated_cacheable_prefix_tokens": cache.get(
                        "estimated_cacheable_prefix_tokens"
                    ),
                    "expected_prefix_tokens": cache.get("expected_prefix_tokens"),
                })
            except Exception:
                continue
        result.sort(key=lambda item: item["captured_at"], reverse=True)
        return result

    @staticmethod
    def load_snapshot(filename: str) -> Optional[Dict[str, Any]]:
        """加载指定快照的完整内容。"""
        path = os.path.join(_SNAPSHOT_DIR, filename)
        if not os.path.isfile(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None

    @staticmethod
    def delete_snapshot(filename: str) -> bool:
        """删除指定快照文件。"""
        path = os.path.join(_SNAPSHOT_DIR, filename)
        with _STORE_LOCK:
            try:
                os.remove(path)
                return True
            except OSError:
                return False

    @staticmethod
    def clear_all_snapshots() -> int:
        """清空所有已保存的快照，返回删除数量。"""
        with _STORE_LOCK:
            if not os.path.isdir(_SNAPSHOT_DIR):
                return 0
            count = 0
            for fname in os.listdir(_SNAPSHOT_DIR):
                if fname.endswith(".json"):
                    try:
                        os.remove(os.path.join(_SNAPSHOT_DIR, fname))
                        count += 1
                    except OSError:
                        continue
            return count

    # ------------------------------------------------------------------
    # 分类
    # ------------------------------------------------------------------

    def _categorize(
            self,
            messages: List[Dict],
            family: Tuple[str, str] = ("", ""),
    ) -> List[Dict[str, Any]]:
        """按 _layer 标签将消息分类为 sections（含内容哈希与上次快照的变更对比）。

        无标签消息按位置推断：进入工具链区域（出现 tool/带 tool_calls 的
        assistant）后，未标记的 system 消息（纠正提示/通知等）归入 tool_chain。
        层标签与变动率元数据来自 context.pipeline 注册中心（单一数据源）。
        family 为 (scope, kind) 前缀族键：变更对比只与同族的上次快照进行。
        """
        import hashlib

        groups: Dict[str, List[Dict]] = {}
        in_chain = False

        for msg in messages:
            layer = msg.get("_layer", "")
            role = msg.get("role", "")

            if role == "tool" or (role == "assistant" and msg.get("tool_calls")):
                in_chain = True
            if not layer:
                if in_chain and role == "system":
                    # 链中注入的纠正/提示/通知（未打标签）归入工具链
                    layer = "tool_chain"
                elif role == "tool":
                    layer = "tool_chain"
                elif role == "assistant" and msg.get("tool_calls"):
                    layer = "tool_chain"
                elif role == "system" and "[执行状态]" in str(msg.get("content", "")):
                    layer = "exec_context"
                elif role in ("user", "assistant"):
                    layer = "conversation"
                else:
                    layer = "volatile"

            content = msg.get("content", "")
            groups.setdefault(layer, []).append({
                "role": role,
                "content": content,
                "chars": len(str(content)),
                "tool_calls": msg.get("tool_calls"),
                "tool_call_id": msg.get("tool_call_id"),
                "_sensitive_result": msg.get("_sensitive_result") is True,
            })

        new_hashes: Dict[Tuple[str, str, str], str] = {}
        new_msg_hashes: Dict[Tuple[str, str, str], List[str]] = {}
        sections: List[Dict[str, Any]] = []

        def _msg_hash(m: Dict) -> str:
            payload = json.dumps(
                {k: m.get(k) for k in ("role", "content", "tool_calls", "tool_call_id")},
                ensure_ascii=False, sort_keys=True, default=str,
            )
            return hashlib.sha1(payload.encode("utf-8", errors="replace")).hexdigest()[:10]

        def _make_section(layer: str, msgs: List[Dict]) -> Dict[str, Any]:
            section_chars = sum(m["chars"] for m in msgs)
            digest = hashlib.sha1(
                "".join(str(m["content"]) for m in msgs).encode("utf-8", errors="replace")
            ).hexdigest()[:12]
            key = (family[0], family[1], layer)
            new_hashes[key] = digest
            msg_hashes = [_msg_hash(m) for m in msgs]
            new_msg_hashes[key] = msg_hashes
            previous = self._last_section_hashes.get(key)
            prev_msg_hashes = self._last_msg_hashes.get(key)
            # 逐消息最长公共前缀：前 N 条与上次快照逐字节一致（可命中缓存），
            # 其后为本轮新增/变化；无基线为 None
            if prev_msg_hashes is None:
                stable_count: Optional[int] = None
            else:
                stable_count = 0
                for cur, prev in zip(msg_hashes, prev_msg_hashes, strict=False):
                    if cur != prev:
                        break
                    stable_count += 1
            meta = get_layer_meta(layer)
            return {
                "layer": layer,
                "label": _layer_label(layer),
                "count": len(msgs),
                "chars": section_chars,
                "estimated_tokens": section_chars // 4,
                "hash": digest,
                # 与上一次快照对比：None=首次快照无基线，True/False=是否变更
                "changed": None if previous is None else previous != digest,
                # 区块内静态/动态切分（None=无基线）
                "stable_count": stable_count,
                "new_count": None if stable_count is None else len(msgs) - stable_count,
                # 变动率元数据（注册中心；Web 展示缓存稳定性依据）
                "volatility": meta.volatility if meta else None,
                "volatility_label": meta.volatility_label if meta else None,
                "messages": redact_tool_messages(msgs),
            }

        order = _layer_order()
        for layer in order:
            msgs = groups.get(layer)
            if msgs:
                sections.append(_make_section(layer, msgs))

        for layer, msgs in groups.items():
            if layer not in order:
                sections.append(_make_section(layer, msgs))

        # 更新该族的基线（重插保序实现 LRU 触达；超容量逐出最久未用的族——
        # reflect 的一次性 scope 每次委托都是新族，无界累积会缓慢泄漏）
        _update_baseline(self._last_section_hashes, new_hashes)
        _update_baseline(self._last_msg_hashes, new_msg_hashes)
        return sections

    @staticmethod
    def _compute_prefix_break(sections: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """全局前缀断链点：按 wire 顺序首个字节分歧位置。

        层首现（无基线）= 本层全量新增；层收缩（stable==count 但整层 hash
        变化，如压缩删除尾部）在层末分叉。全部层均无基线（首次快照）返回
        None；layer=None 表示与上次快照完全一致。
        """
        if not sections or all(s.get("stable_count") is None for s in sections):
            return None
        before_tokens = 0
        for section in sections:
            stable = section.get("stable_count")
            stable_chars = sum(
                m["chars"] for m in section["messages"][:stable or 0]
            )
            if stable is None or stable < section["count"] or section.get("changed") is True:
                return {
                    "layer": section["layer"],
                    "label": section["label"],
                    "index": stable or 0,
                    "before_tokens": before_tokens + stable_chars // 4,
                }
            before_tokens += section["estimated_tokens"]
        return {"layer": None, "label": None, "index": None, "before_tokens": before_tokens}

    @staticmethod
    def _build_cache_block(sections: List[Dict[str, Any]]) -> Dict[str, Any]:
        """构建待完成的观测区块和捕获前的聚合参考，不借用其他调用的用量。

        - estimated_cacheable_prefix_tokens：消息级断链点之前的全部 tokens
          （与上次快照逐字节一致的最长前缀；会话追加等缓存友好变更计入
          既有部分），无基线时 None
        - expected_prefix_tokens：CACHEABLE_PREFIX_LAYERS 中消息的估算 tokens；
          不含工具 schema 和供应商适配，不保证真实缓存命中，也不用于推断平台故障。
        """
        prefix_break = ContextSnapshot._compute_prefix_break(sections)
        if prefix_break is None:
            prefix_tokens: Optional[int] = None
        else:
            prefix_tokens = prefix_break["before_tokens"]

        from agent.llm.prompt_cache import CACHEABLE_PREFIX_LAYERS
        expected_tokens = sum(
            s["estimated_tokens"] for s in sections
            if s.get("layer") in CACHEABLE_PREFIX_LAYERS
        )

        from agent.context.cache_stats import cache_usage_tracker
        return {
            "status": "pending",
            "call": None,
            "recent": cache_usage_tracker.summary(kind="reply"),
            "recent_all": cache_usage_tracker.summary(),
            "estimated_cacheable_prefix_tokens": prefix_tokens,
            "expected_prefix_tokens": expected_tokens,
        }


# 全局单例
context_snapshot = ContextSnapshot()


_BaselineValue = TypeVar("_BaselineValue")


def _update_baseline(store: Dict[Tuple[str, str, str], _BaselineValue],
                     values: Dict[Tuple[str, str, str], _BaselineValue]) -> None:
    for key, value in values.items():
        store.pop(key, None)
        store[key] = value
    while len(store) > _MAX_DIFF_FAMILIES * 4:
        store.pop(next(iter(store)))
