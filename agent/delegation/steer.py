"""子代理转向（Steer）— 运行中委托的两档指令注入。

``send_to_agent`` 对运行中的委托不是排队下一个任务，而是在安全边界插入
消息，可以改变进行中的工作——不必取消重开、不丢失已完成的部分。两档
投递语义（对齐 pi 的 steer/followUp 拆分与 dsh 的固定档位设计）：

- **steer**（默认）：最近的**步骤边界**注入（两次 LLM 调用之间）——需求
  变更、补充约束、方向纠偏，当轮 LLM 即见；
- **after**：**收束边界**注入（子代理本要结束时）——"做完这批后顺便…"
  型追加，注入后续跑而非另起委托；产出语义仍是"最后一段未被打断的
  连续文本"，中途独白照常归档为过程。

Anelf 适配（多频道个人助理语境）：

- **寻址**：按 ``delegation_id``（外部唯一稳定的标识；子代理 reflect 的
  一次性 scope 外部不可知）。前台/后台委托统一支持——谁在运行就可转向。
- **投递**：``SteerInbox``（模块级单例，进程内）按档位暂存消息；
  ``SubAgent.run`` 经 ``bind_steer_drain`` 把"取走本委托消息"的闭包绑进
  ContextVar，think_loop 轮顶 drain（steer 档）/ 收束边界 drain（after 档）。
- **主会话零开销**：ContextVar 未绑定时 drain 恒空（用户插话本有
  ``_fetch_new_user_messages`` 并入机制，无需 steer）。
- **生命周期**：委托结束（无论成败）清箱，防残留指令误入下次同名委托；
  单委托上限 8 条（两档合并计）防轰炸。

Model Experience：注入消息以 user 角色出现在子代理工具链尾部（带
[转向指令]/[追加指令] 标记），当轮 LLM 即见；token 影响 = 消息本身；
缓存影响 = 尾部追加，不动前缀。
"""

from __future__ import annotations

import threading
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Callable, Iterator, List, Optional

from core.log import log

# 投递档位：steer = 步骤边界（改变进行中的工作）；after = 收束边界（结束前追加）
MODE_STEER = "steer"
MODE_AFTER = "after"
VALID_MODES = frozenset({MODE_STEER, MODE_AFTER})

# 单委托暂存上限（两档合并计；超出拒绝：轰炸防护，正常转向远用不到）
_MAX_MESSAGES_PER_DELEGATION = 8

# 消息长度上限（与推送截断同量级，防超大正文撑爆子代理上下文）
_MAX_MESSAGE_CHARS = 4000


class SteerInbox:
    """delegation_id → 按档位待注入的消息列表（进程内注册表）。"""

    def __init__(self) -> None:
        self._boxes: dict[str, List[tuple[str, str]]] = {}
        self._lock = threading.Lock()

    def push(self, delegation_id: str, message: str, mode: str = MODE_STEER) -> bool:
        """暂存一条转向消息；超上限或非法档位返回 False（消息丢弃并记日志）。"""
        message = (message or "").strip()
        if not delegation_id or not message or mode not in VALID_MODES:
            return False
        if len(message) > _MAX_MESSAGE_CHARS:
            message = message[:_MAX_MESSAGE_CHARS] + "…(截断)"
        with self._lock:
            box = self._boxes.setdefault(delegation_id, [])
            if len(box) >= _MAX_MESSAGES_PER_DELEGATION:
                return False
            box.append((mode, message))
            return True

    def drain(self, delegation_id: str, mode: str = MODE_STEER) -> List[str]:
        """取走该委托指定档位的全部暂存消息（不触碰另一档）。"""
        with self._lock:
            box = self._boxes.get(delegation_id)
            if not box:
                return []
            kept = [(m, text) for m, text in box if m != mode]
            drained = [text for m, text in box if m == mode]
            if kept:
                self._boxes[delegation_id] = kept
            else:
                self._boxes.pop(delegation_id, None)
        return drained

    def pending_count(self, delegation_id: str, mode: Optional[str] = None) -> int:
        with self._lock:
            box = self._boxes.get(delegation_id, ())
            if mode is None:
                return len(box)
            return sum(1 for m, _ in box if m == mode)

    def clear(self, delegation_id: str) -> None:
        """委托结束时清箱（防残留指令误入后续执行）。"""
        with self._lock:
            self._boxes.pop(delegation_id, None)


# 模块级单例（对齐 interrupts 的进程内注册表形态）
steer_inbox = SteerInbox()


# ------------------------------------------------------------------
# 思维循环消费桥（ContextVar：委托侧组装，think_loop 只消费）
# ------------------------------------------------------------------

# 绑定的 drain 闭包签名：档位 → 消息列表（SubAgent 绑 steer_inbox.drain 的柯里化）
SteerDrainFn = Callable[[str], List[str]]

_steer_drain_hook: ContextVar[Optional[SteerDrainFn]] = ContextVar(
    "steer_drain_hook", default=None,
)


@contextmanager
def bind_steer_drain(drain: SteerDrainFn) -> Iterator[None]:
    """在当前任务上下文绑定"按档位取走转向消息"的闭包（SubAgent.run 调用）。

    ContextVar 经 create_task 复制进子代理的整个执行树——think_loop
    深处取到的即本委托的 drain；主会话未绑定，恒空。
    """
    token = _steer_drain_hook.set(drain)
    try:
        yield
    finally:
        _steer_drain_hook.reset(token)


def drain_steered_messages(mode: str = MODE_STEER) -> List[str]:
    """取走当前任务绑定的指定档位转向消息（think_loop 调用，fail-open）。"""
    drain = _steer_drain_hook.get()
    if drain is None:
        return []
    try:
        return [m for m in (drain(mode) or []) if m]
    except Exception as exc:
        log(f"转向消息取走失败（已忽略）: {exc}", "WARNING", tag="委托")
        return []
