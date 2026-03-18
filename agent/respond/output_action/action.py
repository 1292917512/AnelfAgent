from __future__ import annotations

from typing import Optional, Union

from agent.messages import Everything, EverythingGroup
from core.log import log

from .output_interface import OutputProtocol, StreamOutputProtocol


class Action:
    """消息输出路由器。

    按 adapter_key 精确路由：keyed output 直达，无匹配走 default output。
    """

    def __init__(self) -> None:
        self._keyed_outputs: dict[str, OutputProtocol] = {}
        self._default_outputs: list[OutputProtocol] = []

    def register_output(self, output: OutputProtocol, adapter_key: str = "") -> None:
        if adapter_key:
            self._keyed_outputs[adapter_key] = output
        else:
            self._default_outputs.append(output)

    def unregister_output(self, adapter_key: str = "") -> bool:
        if adapter_key:
            return self._keyed_outputs.pop(adapter_key, None) is not None
        return False

    def remove_output(self, output: OutputProtocol) -> None:
        """按实例移除（向后兼容）。"""
        self._default_outputs = [o for o in self._default_outputs if o is not output]
        keys_to_remove = [k for k, v in self._keyed_outputs.items() if v is output]
        for k in keys_to_remove:
            del self._keyed_outputs[k]

    def clear_output(self) -> None:
        self._keyed_outputs.clear()
        self._default_outputs.clear()

    def get_output_keys(self) -> list[str]:
        """返回所有已注册的 keyed 通道标识。"""
        return list(self._keyed_outputs.keys())

    # ------------------------------------------------------------------
    # 路由核心
    # ------------------------------------------------------------------

    def _resolve_targets(self, adapter_key: str) -> list[OutputProtocol]:
        if adapter_key and adapter_key in self._keyed_outputs:
            return [self._keyed_outputs[adapter_key]]
        if self._default_outputs:
            return self._default_outputs
        return list(self._keyed_outputs.values())

    @staticmethod
    def _get_adapter_key(anything: Optional[Everything]) -> str:
        return getattr(anything, "adapter_key", "") or ""

    def _describe_route(self, adapter_key: str, targets: list[OutputProtocol]) -> str:
        """生成路由描述（用于日志）。"""
        if adapter_key and adapter_key in self._keyed_outputs:
            return f"[{adapter_key}] (精确匹配)"
        if targets and targets is self._default_outputs:
            return f"[default] ({len(targets)} 个默认输出)"
        names = [getattr(t, "__class__", type(t)).__name__ for t in targets]
        return f"[{adapter_key or 'no-key'}] → {', '.join(names)}"

    # ------------------------------------------------------------------
    # 消息发送（按 adapter_key 路由）
    # ------------------------------------------------------------------

    async def send_message(self, anything: Everything, message: str) -> None:
        """发送消息。发送失败时抛出异常供上层处理。"""
        adapter_key = self._get_adapter_key(anything)
        targets = self._resolve_targets(adapter_key)
        route_info = self._describe_route(adapter_key, targets)

        errors: list[str] = []
        if isinstance(anything, EverythingGroup) and anything.group_id not in (0, "0", "", None):
            log(f"📡 消息路由: group_{anything.group_id} → {route_info}", tag="思维")
            for target in targets:
                try:
                    await target.send_group_msg(anything.group_id, message, anything)
                except Exception as exc:
                    errors.append(f"[{type(target).__name__}] {exc}")
        else:
            log(f"📡 消息路由: user_{anything.uid} → {route_info}", tag="思维")
            for target in targets:
                try:
                    await target.send_private_msg(anything.uid, message, anything)
                except Exception as exc:
                    errors.append(f"[{type(target).__name__}] {exc}")

        if errors:
            raise RuntimeError("消息发送失败: " + "; ".join(errors))

    async def send_group_msg(
        self, group_id: Union[int, str], message: str, anything: Optional[Everything] = None,
    ) -> None:
        for target in self._resolve_targets(self._get_adapter_key(anything)):
            await target.send_group_msg(group_id, message, anything)

    async def send_private_msg(
        self, uid: Union[int, str], message: str, anything: Optional[Everything] = None,
    ) -> None:
        for target in self._resolve_targets(self._get_adapter_key(anything)):
            await target.send_private_msg(uid, message, anything)

    # ------------------------------------------------------------------
    # 流式输出（按 adapter_key 路由）
    # ------------------------------------------------------------------

    async def stream_start(self, anything: Optional[Everything] = None) -> None:
        for out in self._resolve_targets(self._get_adapter_key(anything)):
            if isinstance(out, StreamOutputProtocol):
                await out.stream_start(anything)

    async def stream_chunk(self, chunk: str, anything: Optional[Everything] = None) -> None:
        for out in self._resolve_targets(self._get_adapter_key(anything)):
            if isinstance(out, StreamOutputProtocol):
                await out.stream_chunk(chunk, anything)

    async def stream_end(self, full_text: str, anything: Optional[Everything] = None) -> None:
        adapter_key = self._get_adapter_key(anything)
        targets = self._resolve_targets(adapter_key)
        if full_text.strip():
            route_info = self._describe_route(adapter_key, targets)
            log(f"📡 流式路由: → {route_info}", tag="思维")
        for out in targets:
            if isinstance(out, StreamOutputProtocol):
                await out.stream_end(full_text, anything)
