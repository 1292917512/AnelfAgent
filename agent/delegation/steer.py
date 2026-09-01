"""子代理转向（Steer）— 运行中委托的步骤边界指令注入。

``send_to_agent`` 对运行中的委托不是排队下一个任务，而是在**最近的步骤
边界**插入消息，可以改变进行中的工作——不必取消重开、不丢失已完成的部分。

Anelf 适配（多频道个人助理语境）：

- **寻址**：按 ``delegation_id``（外部唯一稳定的标识；子代理 reflect 的
  一次性 scope 外部不可知）。前台/后台委托统一支持——谁在运行就可转向。
- **投递**：``SteerInbox``（模块级单例，进程内）暂存消息；``SubAgent.run``
  经 ``bind_steer_drain`` 把"取走本委托消息"的闭包绑进 ContextVar，
  think_loop 每轮轮顶 drain——注入发生在两次 LLM 调用之间的安全边界
  （协作式：每轮检查一次，而非抢占）。
- **主会话零开销**：ContextVar 未绑定时 drain 恒空（用户插话本有
  ``_fetch_new_user_messages`` 并入机制，无需 steer）。
- **生命周期**：委托结束（无论成败）清箱，防残留指令误入下次同名委托；
  单委托上限 8 条防轰炸。

Model Experience：注入消息以 user 角色出现在子代理工具链尾部（带
[转向指令] 标记），当轮 LLM 即见；token 影响 = 消息本身；缓存影响 =
尾部追加，不动前缀。
"""

from __future__ import annotations

import threading
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Callable, Iterator, List, Optional

from core.log import log

# 单委托暂存上限（超出拒绝：轰炸防护，正常转向远用不到）
_MAX_MESSAGES_PER_DELEGATION = 8

# 消息长度上限（与推送截断同量级，防超大正文撑爆子代理上下文）
_MAX_MESSAGE_CHARS = 4000


class SteerInbox:
    """delegation_id → 待注入消息列表（进程内注册表）。"""

    def __init__(self) -> None:
        self._boxes: dict[str, List[str]] = {}
        self._lock = threading.Lock()

    def push(self, delegation_id: str, message: str) -> bool:
        """暂存一条转向消息；超上限返回 False（消息丢弃并记日志）。"""
        message = (message or "").strip()
        if not delegation_id or not message:
            return False
        if len(message) > _MAX_MESSAGE_CHARS:
            message = message[:_MAX_MESSAGE_CHARS] + "…(截断)"
        with self._lock:
            box = self._boxes.setdefault(delegation_id, [])
            if len(box) >= _MAX_MESSAGES_PER_DELEGATION:
                return False
            box.append(message)
            return True

    def drain(self, delegation_id: str) -> List[str]:
        """取走并清空该委托的全部暂存消息（think_loop 轮顶消费）。"""
        with self._lock:
            messages = self._boxes.pop(delegation_id, [])
        return messages

    def pending_count(self, delegation_id: str) -> int:
        with self._lock:
            return len(self._boxes.get(delegation_id, ()))

    def clear(self, delegation_id: str) -> None:
        """委托结束时清箱（防残留指令误入后续执行）。"""
        with self._lock:
            self._boxes.pop(delegation_id, None)


# 模块级单例（对齐 interrupts 的进程内注册表形态）
steer_inbox = SteerInbox()


# ------------------------------------------------------------------
# 思维循环消费桥（ContextVar：委托侧组装，think_loop 只消费）
# ------------------------------------------------------------------

_steer_drain_hook: ContextVar[Optional[Callable[[], List[str]]]] = ContextVar(
    "steer_drain_hook", default=None,
)


@contextmanager
def bind_steer_drain(drain: Callable[[], List[str]]) -> Iterator[None]:
    """在当前任务上下文绑定"取走转向消息"的闭包（SubAgent.run 调用）。

    ContextVar 经 create_task 复制进子代理的整个执行树——think_loop
    深处取到的即本委托的 drain；主会话未绑定，恒空。
    """
    token = _steer_drain_hook.set(drain)
    try:
        yield
    finally:
        _steer_drain_hook.reset(token)


def drain_steered_messages() -> List[str]:
    """取走当前任务绑定的全部转向消息（think_loop 轮顶调用，fail-open）。"""
    drain = _steer_drain_hook.get()
    if drain is None:
        return []
    try:
        return [m for m in (drain() or []) if m]
    except Exception as exc:
        log(f"转向消息取走失败（已忽略）: {exc}", "WARNING", tag="委托")
        return []
