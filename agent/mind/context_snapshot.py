"""上下文快照捕获器 — 捕获 LLM 调用的完整上下文并持久化。

布防后在下一次 LLM 调用前捕获完整 messages + tools（含 _layer 分类标签），
捕获后自动解除布防并保存到 logs/context_snapshots/ 目录。

未布防时 try_capture 仅做一次 bool 检查，零开销。
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from typing import Any, Dict, List, Optional

from agent.mind.context_pipeline import get_layer_meta, list_layer_metas
from core.log import log

_SNAPSHOT_DIR = os.path.join("logs", "context_snapshots")


# ======================================================================
# 分类定义（层标签/顺序的单一数据源在 context_pipeline 注册中心）
# ======================================================================

def _layer_label(layer: str) -> str:
    meta = get_layer_meta(layer)
    return meta.label if meta else layer


def _layer_order() -> List[str]:
    return [m.layer for m in list_layer_metas()]


# ======================================================================
# 捕获器
# ======================================================================


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
        # 上一次快照的 section 哈希（layer → sha1 前缀），用于逐 section 变更对比
        self._last_section_hashes: Dict[str, str] = {}
        # 上一次快照的逐消息哈希（layer → [sha1]），用于区块内"已缓存前缀 /
        # 本轮新增"切分（追加式层的新增部分与既有前缀分开呈现）
        self._last_msg_hashes: Dict[str, List[str]] = {}

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
            prefix_drift: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """尝试捕获（在 _invoke_llm_unified 中 normalize 前调用）。

        未布防且未开启连续捕获时立即返回 False（零开销）。
        一次性布防捕获后自动解除；连续模式持续捕获并追加紧凑记录。
        kind 为调用用途（reply/reflect…），随快照记录供列表按用途解读命中率。
        prefix_drift 为 PrefixGuard 的前缀断裂归因（None=前缀稳定/无基线），
        随快照落盘供缓存命中率下跌归因。
        """
        if not self._armed and not self._continuous:
            return False

        async with self._lock:
            oneshot = self._armed
            if not oneshot and not self._continuous:
                return False

            sections = self._categorize(messages)
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
                "captured_at": time.time(),
                "model": model,
                "kind": kind,
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
            }
            self._armed = False

            # 持久化（同步文件写放工作线程，避免持锁阻塞事件循环）
            filename = await asyncio.to_thread(self._save, self._snapshot)
            if self._continuous:
                await asyncio.to_thread(self._append_record, self._snapshot, filename)

            log(
                f"上下文快照已捕获: {len(messages)} msgs, "
                f"{len(tool_names)} tools, ~{estimated_tokens} tokens"
                f"{f', 已保存 {filename}' if filename else ''}",
                tag="快照",
            )
            return True

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
                "captured_at": snapshot.get("captured_at"),
                "file": filename,
                "model": snapshot.get("model"),
                "kind": snapshot.get("kind"),
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
            llm = rt.mind.llm
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
    def _save(snapshot: Dict[str, Any]) -> str:
        """保存快照到 JSON 文件，返回文件名。"""
        try:
            os.makedirs(_SNAPSHOT_DIR, exist_ok=True)
            ts = time.strftime("%Y%m%d_%H%M%S")
            filename = f"snapshot_{ts}.json"
            path = os.path.join(_SNAPSHOT_DIR, filename)
            # 持久化时不保存完整 tools schema（体积大），只保存名称
            save_data = {**snapshot}
            save_data["tools"] = snapshot.get("tool_names", [])
            with open(path, "w", encoding="utf-8") as f:
                json.dump(save_data, f, ensure_ascii=False, indent=2, default=str)
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
                # 列表徽标取捕获时点最近的一次调用（不限用途）：若只看 reply
                # 主口径，一次低命中会粘性盖在随后一串辅助调用捕获行上，
                # 被误读为"连续多次低命中"
                last_call = cache.get("last_call_any") or cache.get("last_call") or {}
                # 前缀字节是否稳定：除每轮必变的工具链/实时注入/执行态外
                # 所有 section 未变（None=无基线无法判定）。命中低而前缀稳定
                # ⇒ 供应商侧缓存波动，列表以"平台波动"标识，与内容断裂导致的
                # 真实低命中区分
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
                    "captured_at": data.get("captured_at", 0),
                    "model": data.get("model", ""),
                    "kind": data.get("kind", ""),
                    "prefix_stable": prefix_stable,
                    "model_context_window": data.get("model_context_window", 0),
                    "estimated_tokens": data.get("estimated_tokens", 0),
                    "message_count": data.get("message_count", 0),
                    "tool_count": data.get("tool_count", 0),
                    # 缓存简况：捕获前最近一次调用的真实命中（无数据为 None）
                    "cache_hit_rate": last_call.get("cache_hit_rate"),
                    "cache_read_input_tokens": last_call.get("cache_read_input_tokens"),
                    "cache_creation_input_tokens": last_call.get("cache_creation_input_tokens"),
                    "estimated_cacheable_prefix_tokens": cache.get(
                        "estimated_cacheable_prefix_tokens"
                    ),
                    "expected_prefix_tokens": cache.get("expected_prefix_tokens"),
                })
            except Exception:
                continue
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
        if not os.path.isfile(path):
            return False
        try:
            os.remove(path)
            return True
        except Exception:
            return False

    @staticmethod
    def clear_all_snapshots() -> int:
        """清空所有已保存的快照，返回删除数量。"""
        if not os.path.isdir(_SNAPSHOT_DIR):
            return 0
        count = 0
        for fname in os.listdir(_SNAPSHOT_DIR):
            if fname.endswith(".json"):
                try:
                    os.remove(os.path.join(_SNAPSHOT_DIR, fname))
                    count += 1
                except Exception:
                    continue
        return count

    # ------------------------------------------------------------------
    # 分类
    # ------------------------------------------------------------------

    def _categorize(self, messages: List[Dict]) -> List[Dict[str, Any]]:
        """按 _layer 标签将消息分类为 sections（含内容哈希与上次快照的变更对比）。

        无标签消息按位置推断：进入工具链区域（出现 tool/带 tool_calls 的
        assistant）后，未标记的 system 消息（纠正提示/通知等）归入 tool_chain。
        层标签与变动率元数据来自 context_pipeline 注册中心（单一数据源）。
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
            })

        new_hashes: Dict[str, str] = {}
        new_msg_hashes: Dict[str, List[str]] = {}
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
            new_hashes[layer] = digest
            msg_hashes = [_msg_hash(m) for m in msgs]
            new_msg_hashes[layer] = msg_hashes
            previous = self._last_section_hashes.get(layer)
            prev_msg_hashes = self._last_msg_hashes.get(layer)
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
                "messages": msgs,
            }

        order = _layer_order()
        for layer in order:
            msgs = groups.get(layer)
            if msgs:
                sections.append(_make_section(layer, msgs))

        for layer, msgs in groups.items():
            if layer not in order:
                sections.append(_make_section(layer, msgs))

        self._last_section_hashes = new_hashes
        self._last_msg_hashes = new_msg_hashes
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
        """构建缓存观测区块：上次调用真实缓存用量 + 两种前缀口径。

        - estimated_cacheable_prefix_tokens：消息级断链点之前的全部 tokens
          （与上次快照逐字节一致的最长前缀；会话追加等缓存友好变更计入
          既有部分），无基线时 None
        - expected_prefix_tokens：按断点锚点布局的理论可命中前缀
          （CACHEABLE_PREFIX_LAYERS 字节稳定层的 tokens 合计）——与本次
          cache_read 对比即可秒判：expected 高而 read=0 ⇒ 非内容漂移
          （网关侧/连接亲和问题）；expected 与 read 同步降 ⇒ 前缀内容变更
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

        from agent.mind.cache_stats import cache_usage_tracker
        # 主口径只看主对话调用（reply）：reflect 评审/心跳分析等辅助调用
        # 无共享前缀，命中率为 0 属正常，混入会误报"缓存崩了"
        last_call = cache_usage_tracker.last(kind="reply")
        if last_call is not None:
            # 标注调用年龄与类型：上次调用可能来自更早的会话（回声），
            # 无年龄标记的 0% 会被误读为当前缓存失效
            last_call = {
                **last_call,
                "age_sec": round(max(0.0, time.time() - last_call["ts"]), 1),
            }
        last_any = cache_usage_tracker.last()
        if last_any is not None:
            last_any = {
                **last_any,
                "age_sec": round(max(0.0, time.time() - last_any["ts"]), 1),
            }
        return {
            "last_call": last_call,
            "last_call_any": last_any,
            "recent": cache_usage_tracker.summary(kind="reply"),
            "recent_all": cache_usage_tracker.summary(),
            "estimated_cacheable_prefix_tokens": prefix_tokens,
            "expected_prefix_tokens": expected_tokens,
        }


# 全局单例
context_snapshot = ContextSnapshot()
