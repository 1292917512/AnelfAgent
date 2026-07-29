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
    "volatile": "短期记忆 + 上下文注入（volatile 层）",
    "overflow": "上下文溢出提示",
    "security": "会话令牌安全标记",
    "memory": "语义召回 + 跨频道 + 技能匹配",
    "conversation": "对话历史",
    "tool_chain": "工具调用链",
    "exec_context": "执行状态上下文",
}


# ======================================================================
# 捕获器
# ======================================================================


class ContextSnapshot:
    """上下文快照捕获器（单例）。"""

    def __init__(self) -> None:
        self._armed: bool = False
        self._snapshot: Optional[Dict[str, Any]] = None
        self._lock = asyncio.Lock()

    @property
    def armed(self) -> bool:
        return self._armed

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

    async def try_capture(
        self,
        messages: List[Dict],
        tools: Optional[List[Dict]],
        model: str,
    ) -> bool:
        """尝试捕获（在 _invoke_llm_unified 中 normalize 前调用）。

        未布防时立即返回 False（零开销）。
        捕获成功后自动解除布防（one-shot）并持久化。
        """
        if not self._armed:
            return False

        async with self._lock:
            if not self._armed:
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
            }
            self._armed = False

            # 持久化（同步文件写放工作线程，避免持锁阻塞事件循环）
            filename = await asyncio.to_thread(self._save, self._snapshot)

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
        """清除内存快照 + 解除布防。"""
        self._armed = False
        self._snapshot = None

    def get_status(self) -> Dict[str, Any]:
        """返回当前状态（API 用）。"""
        return {
            "armed": self._armed,
            "has_snapshot": self._snapshot is not None,
            "captured_at": self._snapshot.get("captured_at") if self._snapshot else None,
            "model": self._snapshot.get("model") if self._snapshot else None,
            "model_context_window": self._snapshot.get("model_context_window") if self._snapshot else None,
            "estimated_tokens": self._snapshot.get("estimated_tokens") if self._snapshot else None,
            "message_count": self._snapshot.get("message_count") if self._snapshot else None,
            "tool_count": self._snapshot.get("tool_count") if self._snapshot else None,
        }

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
        """列出所有已保存的快照（摘要信息）。"""
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
                result.append({
                    "filename": fname,
                    "captured_at": data.get("captured_at", 0),
                    "model": data.get("model", ""),
                    "model_context_window": data.get("model_context_window", 0),
                    "estimated_tokens": data.get("estimated_tokens", 0),
                    "message_count": data.get("message_count", 0),
                    "tool_count": data.get("tool_count", 0),
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

    @staticmethod
    def _categorize(messages: List[Dict]) -> List[Dict[str, Any]]:
        """按 _layer 标签将消息分类为 sections。"""
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

        ordered_layers = [
            "stable", "context", "volatile", "overflow", "security",
            "memory", "conversation", "tool_chain", "exec_context",
        ]
        sections: List[Dict[str, Any]] = []
        for layer in ordered_layers:
            msgs = groups.get(layer)
            if not msgs:
                continue
            section_chars = sum(m["chars"] for m in msgs)
            sections.append({
                "layer": layer,
                "label": LAYER_LABELS.get(layer, layer),
                "count": len(msgs),
                "chars": section_chars,
                "estimated_tokens": section_chars // 4,
                "messages": msgs,
            })

        for layer, msgs in groups.items():
            if layer not in ordered_layers:
                section_chars = sum(m["chars"] for m in msgs)
                sections.append({
                    "layer": layer,
                    "label": layer,
                    "count": len(msgs),
                    "chars": section_chars,
                    "estimated_tokens": section_chars // 4,
                    "messages": msgs,
                })

        return sections


# 全局单例
context_snapshot = ContextSnapshot()
