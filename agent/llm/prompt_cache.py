"""Anthropic Prompt Caching 断点设施 — 请求装饰的唯一权威。

架构纪律（借鉴 Hermes agent/prompt_caching.py，收敛为单一装饰点）：

- **唯一装饰点**：`decorate_request` 在发送边界（llm_invoker）调用，
  管线/think_loop/内容构建侧只负责 `_layer` 标签，谁都不写 cache_control
- 断点只打在发送副本上（copy-on-write），持久化数据与共享上下文永不被改写
- 跨供应商回退由 llm_manager 用 `strip_cache_control_copy` 适配
  （非 Anthropic 候选收剥离副本）
- 断点预算（每请求 ≤ 4，含 wire tools 数组上的断点）按 `_ANCHOR_LAYERS`
  声明式锚点表放置：各层末消息 + 链尾（无 _layer 的末消息）+ tools 末位补位
- TTL 由 `prompt_cache_anthropic_ttl`（5m/1h）驱动

litellm 透传事实（anthropic/chat/transformation.py + prompt_templates/factory.py）：
- system 合并消息的 content 块级 cache_control 原样保留
- role=tool 消息的顶层 cache_control 会移入 anthropic tool_result 块
- tools 数组的顶层 cache_control 原样透传
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from core.config import get_config, get_config_bool

# Anthropic 每请求断点硬上限
MAX_BREAKPOINTS = 4

# 头部锚点层（断点打在各层**末消息**上，覆盖整层前缀；按序消耗预算）
_ANCHOR_LAYERS = ("stable", "context")

# 历史锚点层（conversation 末尾，无历史时回退 summary；由配置门控）
_HISTORY_ANCHOR_LAYERS = ("conversation", "summary")

# 理论可命中前缀层（快照分析口径：这些层字节稳定，其 tokens 即预期 cache_read 下限）
CACHEABLE_PREFIX_LAYERS = ("stable", "context", "summary")


def cache_marker() -> Dict[str, Any]:
    """构造 cache_control marker（TTL 由配置驱动，默认 5m 短缓存）。

    prompt_cache_anthropic_ttl="1h" 时携带 ttl 字段（写入成本 2x vs 1.25x，
    适合会话间隔 >5min 的场景；部分兼容网关不支持，拒绝时调回 5m）。
    """
    ttl = str(get_config("prompt_cache_anthropic_ttl", "5m")).strip().lower()
    if ttl == "1h":
        return {"type": "ephemeral", "ttl": "1h"}
    return {"type": "ephemeral"}


def is_tools_breakpoint_enabled() -> bool:
    """wire tools 数组末尾断点开关（整个工具 schema 前缀进缓存）。"""
    return get_config_bool("prompt_cache_tools_breakpoint", True)


def is_anthropic_wire(model: str, api_type: str) -> bool:
    """Anthropic 线判定（决定是否做断点装饰）：模型名或 api_type 推断 + 总开关。"""
    if not get_config_bool("prompt_cache_anthropic_breakpoint", True):
        return False
    m = (model or "").lower()
    return "anthropic" in m or "claude" in m or (api_type or "").lower() == "anthropic"


def count_breakpoints(messages: List[Dict]) -> int:
    """统计消息列表上已声明的断点数（顶层 + content 块级）。"""
    count = 0
    for msg in messages:
        if msg.get("cache_control") is not None:
            count += 1
        content = msg.get("content")
        if isinstance(content, list):
            count += sum(
                1 for part in content
                if isinstance(part, dict) and part.get("cache_control") is not None
            )
    return count


def apply_tools_breakpoint(tools: Optional[List[Dict]]) -> Optional[List[Dict]]:
    """在 wire tools 数组末尾 schema 注入断点（不改写入参，返回新列表）。

    整个工具数组随末 schema 进入缓存：配合工具排序确定性 + sticky 激活，
    数组跨会话字节稳定；人设/目录文本变更时工具数组前缀仍可命中。
    调用方负责预算门控（断点总数 ≤ MAX_BREAKPOINTS）。
    """
    if not tools:
        return tools
    return [*tools[:-1], {**tools[-1], "cache_control": cache_marker()}]


def _select_anchor_targets(messages: List[Dict]) -> List[Dict]:
    """按声明式锚点表选出断点目标消息（≤ MAX_BREAKPOINTS 个）。

    布局：stable/context 层末消息（覆盖整层前缀）→ 历史末消息
    （conversation 末尾，无历史回退 summary；配置可关）→ 链尾
    （无 _layer 标签的末消息 = think_loop 逐轮追加的工具链；
    随链增长前移，下轮命中本轮增量缓存）。
    """
    targets: List[Dict] = []

    def _last_of(pred: Any) -> Optional[Dict]:
        found: Optional[Dict] = None
        for msg in messages:
            if pred(msg):
                found = msg
        return found

    for layer in _ANCHOR_LAYERS:
        target = _last_of(lambda m, lay=layer: m.get("_layer") == lay)
        if target is not None:
            targets.append(target)
    # 历史锚点（配置门控）
    if get_config_bool("prompt_cache_summary_breakpoint", True):
        target = None
        for layer in _HISTORY_ANCHOR_LAYERS:
            target = _last_of(lambda m, lay=layer: m.get("_layer") == lay)
            if target is not None:
                break
        if target is not None:
            targets.append(target)
    # 链尾锚点：无 _layer 的末消息（管线构建的消息全部带标签，
    # 无标签即 think_loop 追加的工具链/并入的新消息）
    chain_tail = _last_of(lambda m: m.get("_layer") is None)
    if chain_tail is not None:
        targets.append(chain_tail)
    return targets[:MAX_BREAKPOINTS]


def decorate_messages(messages: List[Dict], *, anthropic: bool) -> List[Dict]:
    """消息缓存断点装饰的唯一入口（发送边界调用，copy-on-write，零拷贝优先）。

    - 非 Anthropic 线：消息带断点时返回剥离副本（防御），否则原样返回
    - Anthropic 线：先剥离既有断点（幂等），再按锚点表重放置 ≤4 个

    tools 数组断点不归此管（llm_client 传输层按预算门控补位）。
    入参消息与调用方上下文共享 dict，本函数绝不原地改写。
    """
    if not anthropic:
        if count_breakpoints(messages) == 0:
            return messages
        return strip_cache_control_copy(messages)

    targets = _select_anchor_targets(messages)
    target_ids = {id(m) for m in targets}
    decorated: List[Dict] = []
    for msg in messages:
        has_top = msg.get("cache_control") is not None
        content = msg.get("content")
        has_block = isinstance(content, list) and any(
            isinstance(p, dict) and p.get("cache_control") is not None for p in content
        )
        is_target = id(msg) in target_ids
        if not has_top and not has_block and not is_target:
            decorated.append(msg)  # 零拷贝共享
            continue
        copied = {k: v for k, v in msg.items() if k != "cache_control"}
        if has_block and isinstance(content, list):
            copied["content"] = [
                {k: v for k, v in p.items() if k != "cache_control"}
                if isinstance(p, dict) else p
                for p in content
            ]
        if is_target:
            copied["cache_control"] = cache_marker()
        decorated.append(copied)
    return decorated


def strip_cache_control_copy(messages: List[Dict]) -> List[Dict]:
    """非破坏式剥离：返回无 cache_control 的新列表（仅拷贝含断点的消息）。

    跨供应商回退场景（Hermes _redecorate_prompt_cache_for_provider 同款纪律）：
    断点是 Anthropic 专属字段，泄露到 OpenAI 兼容端点可能被严格校验拒绝；
    原列表与 think_loop 上下文共享 dict，禁止原地剥离。
    """
    result: List[Dict] = []
    for msg in messages:
        has_top = msg.get("cache_control") is not None
        content = msg.get("content")
        has_block = isinstance(content, list) and any(
            isinstance(p, dict) and p.get("cache_control") is not None for p in content
        )
        if not has_top and not has_block:
            result.append(msg)  # 无断点消息零拷贝共享
            continue
        copied = {k: v for k, v in msg.items() if k != "cache_control"}
        if has_block and isinstance(content, list):
            copied["content"] = [
                {k: v for k, v in p.items() if k != "cache_control"}
                if isinstance(p, dict) else p
                for p in content
            ]
        result.append(copied)
    return result


def anthropic_ttl_beta_headers(api_type: str) -> Dict[str, str]:
    """1h TTL 所需的 Anthropic beta 头（5m 或非 Anthropic 线返回空）。

    ttl="1h" 在 Anthropic 官方端点要求 extended-cache-ttl beta 头，
    缺失会 400；兼容网关多余 beta 头通常被忽略（被拒绝时调回 5m）。
    """
    if api_type != "anthropic":
        return {}
    if str(get_config("prompt_cache_anthropic_ttl", "5m")).strip().lower() == "1h":
        return {"anthropic-beta": "extended-cache-ttl-2025-04-11"}
    return {}
