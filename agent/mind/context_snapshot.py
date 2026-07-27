"""上下文快照捕获器 — 一次性捕获下一次 LLM 调用的完整上下文。

用户在思维链路页面点击"快照"按钮后布防，系统在下一次 LLM 调用前
捕获完整的 messages + tools（含 _layer 分类标签），捕获后自动解除布防。

未布防时 try_capture 仅做一次 bool 检查，零开销。
快照只存内存，不落盘，clear() 后即消失。
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, List, Optional

from core.log import log


# ======================================================================
# 分类定义
# ======================================================================

LAYER_LABELS: Dict[str, str] = {
    "stable": "人设 + 工具提示（stable 层）",
    "context": "便签等低频内容（context 层）",
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
    """一次性上下文快照捕获器（单例）。"""

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
            self._snapshot = None
            log("上下文快照已布防", "DEBUG", tag="快照")

    async def try_capture(
        self,
        messages: List[Dict],
        tools: Optional[List[Dict]],
        model: str,
    ) -> bool:
        """尝试捕获（在 _invoke_llm_unified 中 normalize 前调用）。

        未布防时立即返回 False（零开销）。
        捕获成功后自动解除布防（one-shot）。
        """
        if not self._armed:
            return False

        async with self._lock:
            if not self._armed:
                return False  # double-check

            sections = self._categorize(messages)
            tool_names = [
                (t.get("function", {}) or {}).get("name", "")
                for t in (tools or [])
            ]

            self._snapshot = {
                "captured_at": time.time(),
                "model": model,
                "message_count": len(messages),
                "tool_count": len(tools) if tools else 0,
                "tool_names": tool_names,
                "tools": tools or [],
                "sections": sections,
            }
            self._armed = False
            log(
                f"上下文快照已捕获: {len(messages)} msgs, "
                f"{len(tool_names)} tools, {len(sections)} sections",
                tag="快照",
            )
            return True

    def get(self) -> Optional[Dict[str, Any]]:
        """获取快照（API 读取）。"""
        return self._snapshot

    def clear(self) -> None:
        """清除快照 + 解除布防。"""
        self._armed = False
        self._snapshot = None

    def get_status(self) -> Dict[str, Any]:
        """返回当前状态（API 用）。"""
        return {
            "armed": self._armed,
            "has_snapshot": self._snapshot is not None,
            "captured_at": self._snapshot.get("captured_at") if self._snapshot else None,
            "model": self._snapshot.get("model") if self._snapshot else None,
            "message_count": self._snapshot.get("message_count") if self._snapshot else None,
            "tool_count": self._snapshot.get("tool_count") if self._snapshot else None,
        }

    # ------------------------------------------------------------------
    # 分类
    # ------------------------------------------------------------------

    @staticmethod
    def _categorize(messages: List[Dict]) -> List[Dict[str, Any]]:
        """按 _layer 标签将消息分类为 sections。

        每条消息的 _layer 由 build_llm_context 在组装时打标，
        normalize_for_send 在发送前剥离（capture 在 normalize 前执行，标签尚存）。
        无 _layer 标签的消息（如 think_loop 追加的 tool_chain / exec_context）
        按角色和内容模式推断分类。
        """
        groups: Dict[str, List[Dict]] = {}

        for msg in messages:
            layer = msg.get("_layer", "")
            role = msg.get("role", "")

            if not layer:
                # 无标签消息：按角色推断
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

            groups.setdefault(layer, []).append({
                "role": role,
                "content": msg.get("content", ""),
                "tool_calls": msg.get("tool_calls"),
                "tool_call_id": msg.get("tool_call_id"),
            })

        # 按固定顺序输出
        ordered_layers = [
            "stable", "context", "volatile", "overflow", "security",
            "memory", "conversation", "tool_chain", "exec_context",
        ]
        sections: List[Dict[str, Any]] = []
        for layer in ordered_layers:
            msgs = groups.get(layer)
            if not msgs:
                continue
            sections.append({
                "layer": layer,
                "label": LAYER_LABELS.get(layer, layer),
                "count": len(msgs),
                "messages": msgs,
            })

        # 未知分类兜底
        for layer, msgs in groups.items():
            if layer not in ordered_layers:
                sections.append({
                    "layer": layer,
                    "label": layer,
                    "count": len(msgs),
                    "messages": msgs,
                })

        return sections


# 全局单例
context_snapshot = ContextSnapshot()
