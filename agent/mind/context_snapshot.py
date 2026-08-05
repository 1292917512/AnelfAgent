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

from core.log import log

_SNAPSHOT_DIR = os.path.join("logs", "context_snapshots")


# ======================================================================
# 分类定义
# ======================================================================

LAYER_LABELS: Dict[str, str] = {
    "stable": "人设 + 工具提示 + 静态指南（stable 层）",
    "context": "动态便签 + 文件索引（context 层）",
    "summary": "早期对话摘要（折叠周期内固定）",
    "conversation": "对话历史（原始窗口）",
    "status": "记忆系统状态（心跳维护）",
    "profile": "实体画像注入",
    "volatile": "短期记忆（volatile 层）",
    "memory": "语义召回 + 跨频道 + 技能匹配",
    "provider": "上下文提供者注入",
    "overflow": "上下文溢出提示",
    "security": "会话令牌安全标记",
    "tool_chain": "工具调用链",
    "exec_context": "执行状态上下文",
}

# 分层在消息序列中的固定顺序（快照 section 排序与缓存前缀估算共用）
_LAYER_ORDER = [
    "stable", "context", "summary", "conversation",
    "status", "profile", "volatile", "memory", "provider",
    "overflow", "security", "tool_chain", "exec_context",
]


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
    ) -> bool:
        """尝试捕获（在 _invoke_llm_unified 中 normalize 前调用）。

        未布防且未开启连续捕获时立即返回 False（零开销）。
        一次性布防捕获后自动解除；连续模式持续捕获并追加紧凑记录。
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
                "model_context_window": model_context_window,
                "estimated_tokens": estimated_tokens,
                "message_count": len(messages),
                "tool_count": len(tools) if tools else 0,
                "tool_names": tool_names,
                "tools": tools or [],
                "sections": sections,
                "cache": self._build_cache_block(sections),
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
                    }
                    for s in snapshot.get("sections", [])
                ],
                "cache": snapshot.get("cache"),
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
            from agent.runtime.singleton import get_runtime
            rt = get_runtime()
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
                last_call = cache.get("last_call") or {}
                result.append({
                    "filename": fname,
                    "captured_at": data.get("captured_at", 0),
                    "model": data.get("model", ""),
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
        """按 _layer 标签将消息分类为 sections（含内容哈希与上次快照的变更对比）。"""
        import hashlib

        groups: Dict[str, List[Dict]] = {}

        for msg in messages:
            layer = msg.get("_layer", "")
            role = msg.get("role", "")

            if not layer:
                if role == "tool":
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
        sections: List[Dict[str, Any]] = []

        def _make_section(layer: str, msgs: List[Dict]) -> Dict[str, Any]:
            section_chars = sum(m["chars"] for m in msgs)
            digest = hashlib.sha1(
                "".join(str(m["content"]) for m in msgs).encode("utf-8", errors="replace")
            ).hexdigest()[:12]
            new_hashes[layer] = digest
            previous = self._last_section_hashes.get(layer)
            return {
                "layer": layer,
                "label": LAYER_LABELS.get(layer, layer),
                "count": len(msgs),
                "chars": section_chars,
                "estimated_tokens": section_chars // 4,
                "hash": digest,
                # 与上一次快照对比：None=首次快照无基线，True/False=是否变更
                "changed": None if previous is None else previous != digest,
                "messages": msgs,
            }

        for layer in _LAYER_ORDER:
            msgs = groups.get(layer)
            if msgs:
                sections.append(_make_section(layer, msgs))

        for layer, msgs in groups.items():
            if layer not in _LAYER_ORDER:
                sections.append(_make_section(layer, msgs))

        self._last_section_hashes = new_hashes
        return sections

    @staticmethod
    def _build_cache_block(sections: List[Dict[str, Any]]) -> Dict[str, Any]:
        """构建缓存观测区块：上次调用真实缓存用量 + 本次快照的可命中前缀估算。

        可命中前缀估算：从头连续未变更的 section 累计 tokens
        （近似供应商前缀缓存的最长可复用前缀，供对比"这次变更差多少缓存"）。
        首次快照无对比基线时为 None（前端显示 —，避免误导性的 0）。
        """
        prefix_tokens: Optional[int] = 0
        for section in sections:
            if section.get("changed") is False:
                prefix_tokens += section["estimated_tokens"]
            elif section.get("changed") is None:
                # 首次快照无基线：无法判断稳定性
                prefix_tokens = None
                break
            else:
                break

        from agent.mind.cache_stats import cache_usage_tracker
        last_call = cache_usage_tracker.last()
        return {
            "last_call": last_call,
            "recent": cache_usage_tracker.summary(),
            "estimated_cacheable_prefix_tokens": prefix_tokens,
        }


# 全局单例
context_snapshot = ContextSnapshot()
