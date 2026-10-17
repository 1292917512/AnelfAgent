"""think_loop 测试共享替身：FakePfc / FakeMind / LLM 轮次结果工厂 / 循环启动器。

各 mind 测试此前各自复制"最小 Mind 替身"样板（pfc 桩方法 + 轮次队列 +
deliver 拦截 + 10 个 kwargs 的 think_loop 调用），此处收敛为唯一实现：
- FakePfc：全部桩方法的超集（含待处理任务状态 / adapter 路由 / 媒体收集）
- FakeMind：rounds 队列逐轮弹出，耗尽后回落 default_text（None 则抛错防哑失败）；
  llm_calls / tool_choices / sent_messages / executed_tools 自动记录
- 变体行为（挂起等待、前言脚本、录制）在用例文件内以子类扩展，勿再复制基类
"""

from __future__ import annotations

import json
import time
from types import SimpleNamespace
from typing import Any, List, Optional
from unittest.mock import AsyncMock

from agent.mind.execution.loop import think_loop
from agent.mind.execution.state import ThinkMode
from core.tags import etag_all
from core.tool_results import extract_error_text

DEFAULT_TOOL_RESULT = '{"ok": true}'

DEFAULT_MIND_CONFIG: dict = {
    "llm_timeout": 10.0,
    "force_tool_use": False,
    "background_wait_timeout": 30.0,
    "background_wait_budget": 120.0,
}


def text_result(text: str) -> SimpleNamespace:
    """一轮 LLM 响应：纯文本（无工具调用 → 收敛回复）。"""
    return SimpleNamespace(
        content=text, tool_calls=[], reasoning_content="",
        usage=None, raw=None, model="fake",
    )


def tool_result(text: str, tool_names: List[str], arguments: str = "{}") -> SimpleNamespace:
    """一轮 LLM 响应：发起若干工具调用（共享同一份 arguments）。"""
    return SimpleNamespace(
        content=text,
        tool_calls=[
            SimpleNamespace(
                id=f"tc_{n}", name=n, arguments=arguments,
                raw={"id": f"tc_{n}", "type": "function",
                     "function": {"name": n, "arguments": arguments}},
            )
            for n in tool_names
        ],
        reasoning_content="", usage=None, raw=None, model="fake",
    )


def end_reply_result() -> SimpleNamespace:
    """一轮 LLM 响应：end_reply 终止指令。"""
    return tool_result("", ["end_reply"])


class FakePfc:
    """think_loop 依赖的全部 PFC 桩方法超集。

    exec_layer=True 时执行上下文带 _layer 标签（对齐真实管线的布局断言）。
    """

    def __init__(self, exec_layer: bool = False) -> None:
        self.exec_layer = exec_layer
        self.pending_tasks: list = []
        self.message_revision = 0
        self.adapter_keys: dict = {}

    def build_execution_context(self, *a, **kw) -> dict:
        ctx = {"role": "system", "content": "exec"}
        if self.exec_layer:
            ctx["_layer"] = "exec_context"
        return ctx

    def add_temporary(self, clip) -> None:
        pass

    def clear_dynamic_tools(self) -> None:
        pass

    def record_tool_use(self, name: str) -> None:
        pass

    def expand_discovered_tools(self, tool_calls, scope: str = "") -> None:
        pass

    def peek_all_tasks(self) -> list:
        return list(self.pending_tasks)

    def get_adapter_key(self, scope: str) -> str:
        return self.adapter_keys.get(scope, "")

    def consume_scope_task(self, scope) -> bool:
        return True

    def collect_images(self, scope: str = "") -> list:
        return []

    def collect_media(self, scope: str = "") -> list:
        return []

    def activate_media_tools(self, images: list, media_segments: list) -> None:
        pass

    async def get_active_tool_schemas(self, adapter_key: str = "", scope: str = "") -> list:
        # think_loop 版本元组含 EntityRegistry.version()：注册表在测试过程中
        # 被其他用例增删时会触发 active_tools 重建，打桩需提供该方法
        return []

    async def get_reflect_tool_schemas(
        self, adapter_key: str = "", scope: str = "", selectors: list | None = None,
    ) -> list:
        # REFLECT 模式重建路径的精简目录打桩（与上方同理）
        return []


class FakeMind:
    """最小 Mind 替身：LLM 按 rounds 队列逐轮响应。

    - rounds 耗尽后回落 default_text；default_text=None 时抛 IndexError
      （脚本化用例轮次耗尽应响亮失败，而非静默编造回复）
    - tool_results 可按工具名定制执行结果（如 send_message 的成功载荷）
    """

    def __init__(
        self,
        rounds: Optional[List[SimpleNamespace]] = None,
        default_text: Optional[str] = "我先说两句～",
        config_overrides: Optional[dict] = None,
        tool_results: Optional[dict] = None,
        pfc: Optional[FakePfc] = None,
    ) -> None:
        self.pfc = pfc if pfc is not None else FakePfc()
        self.context = SimpleNamespace(build_execution_context=self.pfc.build_execution_context)
        self.compressor = None
        self.memory_store = None
        self._rounds: List[SimpleNamespace] = list(rounds or [])
        self.default_text = default_text
        self.llm_calls = 0
        self.tool_choices: list = []
        self.sent_messages: List[list] = []
        self.executed_tools: List[str] = []
        self.tool_results = tool_results or {}
        self._config = {**DEFAULT_MIND_CONFIG, **(config_overrides or {})}
        self._add_system_context = AsyncMock()
        self._reply_adapter_key = ""
        self.conversation_data = SimpleNamespace()
        self.model_invoker = FakeModelInvoker(self)

    @staticmethod
    def _resolve_scope(anything) -> tuple:
        """对齐 Mind._resolve_scope（替身实体缺 scope 字段时回落 uid 维度）。"""
        return (
            getattr(anything, "scope_type", "user"),
            getattr(anything, "scope_id", str(getattr(anything, "uid", ""))),
        )

    def _resolve_adapter_key(self) -> str:
        return ""

    @property
    def tool_executor(self):
        async def _exec(tc) -> str:
            self.executed_tools.append(tc.name)
            result = self.tool_results.get(tc.name, DEFAULT_TOOL_RESULT)
            if tc.name in {"send_message", "send_photo", "send_voice", "send_file"}:
                from agent.channel.delivery import record_delivery
                payload = json.loads(result)
                if isinstance(payload, dict) and payload.get("success") is True and not extract_error_text(payload):
                    tags = dict(etag_all(str(payload.get("delivery", ""))))
                    if tags.get("delivery_status") == "sent" and payload.get("outcome") != "unknown":
                        record_delivery(tags.get("delivery_scope", ""))
            return result
        return _exec

    def _set_phase(self, phase) -> None:
        pass

    def _get_mind_config(self):
        return SimpleNamespace(**self._config)


class FakeModelInvoker:
    """按脚本响应模型调用，记录调用参数。"""

    def __init__(self, mind: FakeMind) -> None:
        self._mind = mind

    def get_context_length(self) -> int:
        return 0

    async def invoke(
        self, messages, tools, anything=None, *, tool_choice=None, options=None,
        stream=False, on_delta=None, purpose="reply", cache_tail_anchor=True,
        abort_event=None, on_tool_call_ready=None,
    ):
        mind = self._mind
        mind.llm_calls += 1
        mind.tool_choices.append(tool_choice)
        mind.sent_messages.append(list(messages))
        if mind._rounds:
            return mind._rounds.pop(0)
        if mind.default_text is None:
            raise IndexError("脚本轮次耗尽（FakeMind 未提供 default_text）")
        return text_result(mind.default_text)


def run_think_loop(
    mind: Any,
    *,
    anything: Any = None,
    mode: ThinkMode = ThinkMode.REPLY,
    steps: Optional[list] = None,
    chain: Optional[list] = None,
    tools: Optional[list] = None,
    collected_text: Optional[list] = None,
    safety_limit: int = 20,
    base_messages: Optional[list] = None,
    adapter_key: Optional[str] = None,
    completion: Optional[dict] = None,
    require_output: bool = False,
):
    """统一的 think_loop 启动器（收敛 10 个 kwargs 的调用样板）。"""
    kwargs: dict = {}
    if adapter_key is not None:
        kwargs["adapter_key"] = adapter_key
    if completion is not None:
        kwargs["completion"] = completion
    return think_loop(
        mind,
        mode=mode,
        tool_chain=chain if chain is not None else [],
        execution_steps=steps if steps is not None else [],
        start_time=time.time(),
        safety_limit=safety_limit,
        collected_text=collected_text if collected_text is not None else [],
        active_tools=tools if tools is not None else [],
        require_output=require_output,
        anything=anything,
        base_messages=base_messages if base_messages is not None
        else [{"role": "user", "content": "你好"}],
        **kwargs,
    )
