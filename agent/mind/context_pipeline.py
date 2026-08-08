"""上下文构建管线 — 所有上下文内容的统一注册中心与组装器。

设计动机：LLM 上下文由十余种内容块组成（人设/工具/便签/摘要/历史/画像/
召回/短期记忆/执行状态…），它们的唯一本质差异是**变动频率**。供应商前缀
缓存要求消息按变动频率从静到动排列——本管线把"排序规则"从手写拼接代码
变为每个块的声明式属性：

- 每个构建块用 @context_block(layer, volatility, label) 装饰器声明，
  装饰时自动注册层元数据（单一数据源，Web 展示/快照分层以此为依据）
- volatility 越大变动越频繁，排序越靠后（稳定前缀 → 缓存命中）
- 管线统一负责：排序组装、_layer 标签、Anthropic 断点注入、布局回退
- think_loop 逐轮管理的层（tool_chain/exec_context）同样注册在案
  （managed="think_loop"），全集即完整上下文

新增内容块只需写一个带装饰器的方法，无需改动组装逻辑。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Awaitable, Callable, Dict, List, Optional, Union

if TYPE_CHECKING:
    from agent.messages import Everything

# ------------------------------------------------------------------
# 变动率等级（值越大变动越频繁，组装排序越靠后）
# ------------------------------------------------------------------

VOL_STABLE = 0        # 人设 / 工具目录：极少变（人设切换、工具注册）
VOL_LOW = 10          # 便签 + 文件索引：人工编辑时变
VOL_PERIODIC = 20     # 对话摘要：折叠周期（默认 20 条消息）才变
VOL_HISTORY = 30      # 对话历史：纯追加（前缀稳定）
VOL_SESSION = 40      # 状态/画像/短期记忆/召回/技能/Provider：每会话重建
VOL_MESSAGE = 50      # 溢出提示 / 安全提示：随消息状态变
VOL_CHAIN = 60        # 工具调用链：每轮追加（think_loop 管理，不经管线）
VOL_ROUND = 90        # exec_context：每轮重建（think_loop 追加在尾部）

# 头部缓存锚点层（Anthropic 断点 1~3，按序）
_HEAD_ANCHOR_LAYERS = ("stable", "context")


# ------------------------------------------------------------------
# 层注册中心（所有上下文内容的单一元数据源）
# ------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LayerMeta:
    """上下文层元数据：标识 + 变动率 + 展示名 + 构建责任方。"""

    layer: str
    volatility: int
    label: str
    # 构建责任方：pipeline=组装管线（base 上下文）/ think_loop=思维循环（逐轮追加）
    managed: str = "pipeline"

    @property
    def volatility_label(self) -> str:
        """变动率的人类可读分档（Web 展示用）。"""
        if self.volatility <= VOL_STABLE:
            return "静态"
        if self.volatility <= VOL_LOW:
            return "低频"
        if self.volatility <= VOL_PERIODIC:
            return "周期"
        if self.volatility <= VOL_HISTORY:
            return "追加"
        if self.volatility <= VOL_MESSAGE:
            return "每会话"
        return "每轮"


_LAYER_REGISTRY: Dict[str, LayerMeta] = {}


def register_layer(layer: str, volatility: int, label: str, *, managed: str = "pipeline") -> None:
    """注册上下文层元数据（重复注册以最后声明为准）。"""
    _LAYER_REGISTRY[layer] = LayerMeta(layer, volatility, label, managed)


def get_layer_meta(layer: str) -> Optional[LayerMeta]:
    """查询层元数据（未注册返回 None）。"""
    return _LAYER_REGISTRY.get(layer)


def list_layer_metas() -> List[LayerMeta]:
    """按变动率从静到动列出全部已注册层（Web 展示/快照排序的依据）。"""
    return sorted(_LAYER_REGISTRY.values(), key=lambda m: (m.volatility, m.layer))


def get_layer_order() -> List[str]:
    """分层排序表（由注册表推导，变动率升序）。"""
    return [m.layer for m in list_layer_metas()]


# 思维循环逐轮管理的层（不经管线组装，但同属上下文体系，统一注册）
register_layer("tool_chain", VOL_CHAIN, "工具调用链", managed="think_loop")
register_layer("exec_context", VOL_ROUND, "执行状态上下文", managed="think_loop")


@dataclass(slots=True)
class ContextInput:
    """上下文构建输入（recollection 准备 + 构建期中间态共享）。"""

    # 预构建文本（分层缓存产物）
    persona_text: str = ""
    tools_text: str = ""
    context_text: str = ""
    status_text: str = ""
    # 召回产物
    memory_msgs: List[Dict] = field(default_factory=list)
    profile_msgs: List[Dict] = field(default_factory=list)
    relation_msgs: List[Dict] = field(default_factory=list)
    goal_msgs: List[Dict] = field(default_factory=list)
    summary_row: Optional[Dict] = None
    # 会话信息
    anything: Optional["Everything"] = None
    adapter_key: str = ""
    scope: str = ""
    prefetched_conversation: Optional[List[Dict]] = None
    anthropic_breakpoint: bool = False
    # 构建期中间态（conversation 块写入，overflow 块读取）
    conversation_list: List[Dict] = field(default_factory=list)
    max_conversation_size: int = 0


# 构建块函数签名：接收共享输入，返回消息列表（角色可自定义，缺省 system）
BlockFn = Callable[[ContextInput], Union[List[Dict], Awaitable[List[Dict]]]]


def context_block(layer: str, volatility: int, label: str = ""):
    """声明上下文构建块：分层标识 + 变动率（排序依据，越小越靠前）+ 展示名。

    装饰时自动把层元数据注册到注册中心；同层多块（如 stable 的人设/工具块）
    以首个非空 label 为准，空 label 不覆盖已注册展示名。
    被装饰方法签名为 (self, inp: ContextInput) -> List[Dict]（可 async），
    返回空列表表示该块本次不注入。
    """
    if label or layer not in _LAYER_REGISTRY:
        register_layer(layer, volatility, label or layer)

    def decorator(fn: BlockFn) -> BlockFn:
        fn._context_block_meta = (layer, volatility)  # type: ignore[attr-defined]
        return fn
    return decorator


class ContextPipeline:
    """上下文构建管线：从宿主对象收集声明块，按变动率排序组装。

    Args:
        host: 持有 @context_block 方法的对象（通常是 ContextAssembly）。
        volatility_overrides: 层名 → 变动率覆盖（legacy 布局回退用：
            把会话级动态层移到历史之前，无需改动块声明）。
    """

    def __init__(
        self,
        host: object,
        volatility_overrides: Optional[Dict[str, int]] = None,
    ) -> None:
        self._overrides = volatility_overrides or {}
        # (volatility, layer, fn)；同变动率按层名排序保证确定性
        builders: List[tuple[int, str, BlockFn]] = []
        for name in dir(host):
            fn = getattr(host, name)
            meta = getattr(fn, "_context_block_meta", None)
            if meta:
                layer, vol = meta
                builders.append((vol, layer, fn))
        builders.sort(key=lambda b: (b[0], b[1]))
        self._builders = builders

    async def build(self, inp: ContextInput) -> List[Dict]:
        """按变动率从静到动组装全部内容块，注入 _layer 标签与缓存断点。"""
        import inspect

        messages: List[Dict] = []
        for _vol, layer, fn in self._builders:
            result = fn(inp)
            blocks = await result if inspect.isawaitable(result) else result
            for msg in blocks:
                if msg.get("content") in (None, ""):
                    continue
                msg.setdefault("role", "system")
                msg["_layer"] = layer
                messages.append(msg)

        if self._overrides:
            # legacy 布局：按覆盖的变动率重排（块内容不变）
            messages.sort(key=lambda m: self._sort_key(m["_layer"]))

        if inp.anthropic_breakpoint:
            self._inject_breakpoints(messages)
        return messages

    def _sort_key(self, layer: str) -> tuple[int, str]:
        vol = self._overrides.get(layer)
        if vol is not None:
            return (vol, layer)
        for v, l, _fn in self._builders:
            if l == layer:
                return (v, layer)
        return (VOL_MESSAGE, layer)

    @staticmethod
    def _inject_breakpoints(messages: List[Dict]) -> None:
        """注入 Anthropic 缓存断点（限额 4）：头部锚点 3 个 + 历史末尾 1 个。"""
        anchors = 0
        for msg in messages:
            if msg.get("_layer") in _HEAD_ANCHOR_LAYERS and anchors < 3:
                msg["cache_control"] = {"type": "ephemeral"}
                anchors += 1
        # 第 4 断点：对话历史末尾（纯追加窗口，断点随之前移）；无历史回退摘要块
        from core.config import get_config_bool
        if not get_config_bool("prompt_cache_summary_breakpoint", True):
            return
        target: Optional[Dict] = None
        for msg in messages:
            if msg.get("_layer") == "conversation":
                target = msg  # 迭代结束即最后一条历史
        if target is None:
            for msg in messages:
                if msg.get("_layer") == "summary":
                    target = msg
        if target is not None:
            target["cache_control"] = {"type": "ephemeral"}
