"""全模型缓存与用量健康门（litellm 升级同步规范，env-gated 默认 skip）。

规范：litellm 版本变动（升级/降级/替换）后必须运行本门。litellm 的流式
usage 处理对本项目是脆弱契约——1.100 曾对未收录模型用本地 tiktoken 估算
伪造 usage（prompt 虚高 ~1.8 倍、completion 清零、缓存 details 丢弃，
2026-09 实证把命中率显示打成 ~50% 假象）。本门对配置内全部启用的 chat
模型逐一经真实 LLMClient 管线（含 usage 旁路）发两次同前缀流式调用，
断言四类健康信号：

1. 两次调用 usage 都必须回报（缺失 = 记账/压缩/缓存观测全部失真，硬失败）；
2. completion_tokens > 0（伪造指纹：litellm 估算路径清零 completion）；
3. 含缓存口径下 read+creation ≤ prompt（尺度混血指纹：真实 read 配伪造
   prompt 必然超界）；
4. 端点可观测时第二次调用 cached>0 且命中率 ≥ 0.7（判别区间：伪造 usage
   尺度混血 ≈0.55，平台缓存粒度确定性损失实测下限 ≈0.78；
   不可观测模型跳过缓存断言，前三项仍然生效）。

Responses 路径说明：openai 系模型的 Responses 流式 usage 当前透传正常
（实测），不另设旁路——本门覆盖 responses 模型，litellm 若在该路径引入
同类伪造会被断言 2/3 当场捕获。

运行：
    LLM_CACHE_E2E=1 uv run pytest tests/integration/test_llm_cache_hit_e2e.py
    # 子集：LLM_CACHE_E2E=1 LLM_CACHE_E2E_MODELS=glm-5.3,k3-1m ...
注意：消耗真实 token（每模型两次数千 tokens 流式调用）。
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

from agent.llm.types import UsageInfo
from core.path import ConfigPaths

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("LLM_CACHE_E2E") != "1",
        reason="set LLM_CACHE_E2E=1（litellm 升级同步规范门，消耗真实 token）",
    ),
]

# 足够长的稳定前缀（越过各供应商最小缓存粒度与冷启动阈值），确保可缓存
_STABLE_PREFIX = (
    "你是一个测试助手。以下是用于验证前缀缓存命中率的稳定背景知识，"
    "请在后续回答中记住它们。"
) + ("背景知识条目：前缀缓存要求请求前缀字节级稳定，任何 system/tools/消息 "
     "前段的改动都会使缓存失效。" * 150)

_MESSAGES: List[Dict[str, Any]] = [
    {"role": "system", "content": _STABLE_PREFIX},
    {"role": "user", "content": "用一句话说明前缀缓存的意义。"},
]

# 重复前缀第二次调用的期望命中率下限。判别区间参考：litellm 伪造 usage
# 的尺度混血命中率 ≈0.55（中文内容 tiktoken 估算虚高 ~1.8 倍）；平台缓存
# 粒度/上限的确定性损失实测下限 ≈0.78（阿里自动缓存仅写入前 27 个 128-
# token 块）。0.7 在两者之间留出判别余量
_MIN_EXPECTED_HIT_RATE = 0.7


def _chat_model_ids() -> List[str]:
    """配置内的启用 chat 模型清单（type_priorities.chat 有效顺序）。"""
    try:
        data = json.loads(
            Path(ConfigPaths.LLM_CLIENTS).read_text(encoding="utf-8"))
    except Exception:
        return []
    enabled = {
        m.get("id")
        for p in data.get("providers", [])
        for m in p.get("models", [])
        if m.get("enabled", True) and "chat" in (m.get("model_types") or [])
    }
    ordered = [mid for mid in data.get("type_priorities", {}).get("chat", [])
               if mid in enabled]
    # 不在优先级列表里的启用 chat 模型一并覆盖
    ordered.extend(sorted(enabled - set(ordered)))
    subset = os.getenv("LLM_CACHE_E2E_MODELS", "")
    if subset.strip():
        wanted = {x.strip() for x in subset.split(",") if x.strip()}
        ordered = [mid for mid in ordered if mid in wanted]
    return ordered


@pytest.fixture(scope="module")
def llm_manager():
    """按真实配置装配的 LLMManager（与线上同一配置与映射，单一权威）。"""
    from agent.llm.llm_manager import LLMManager
    return LLMManager()


async def _call_once(client: Any) -> Optional[UsageInfo]:
    """经真实管线（含 usage 旁路）发一次流式调用，返回 usage。"""
    usage: Optional[UsageInfo] = None
    async for delta in client.chat_stream(_MESSAGES, options={"max_tokens": 8}):
        if delta.usage is not None:
            usage = delta.usage
    return usage


async def _call_cold_then_warm(client: Any) -> tuple[Optional[UsageInfo], Optional[UsageInfo]]:
    """冷调用写缓存后按递增窗口暖调用，取命中最佳的一次。

    供应商缓存写入→可读传播延迟方差大（实测 8s~20s+），只暖一次会把
    传播滞后误报为缓存异常；达预期命中即提前返回，避免无谓调用。
    """
    cold = await _call_once(client)
    best: Optional[UsageInfo] = None
    for wait in (2, 4, 8):
        await asyncio.sleep(wait)
        warm = await _call_once(client)
        if warm is not None and (
                best is None or warm.cache_hit_rate > best.cache_hit_rate):
            best = warm
        if warm is not None and warm.cache_hit_rate >= _MIN_EXPECTED_HIT_RATE:
            break
    return cold, best


@pytest.mark.parametrize("model_id", _chat_model_ids())
async def test_model_cache_and_usage_health(model_id: str, llm_manager) -> None:
    client = llm_manager.get_enabled_client(model_id)
    assert client is not None, f"模型 {model_id} 未启用或不存在"

    cold, warm = await _call_cold_then_warm(client)

    assert cold is not None and warm is not None, (
        f"{model_id} 端点未回报 usage——记账/压缩/缓存观测全部失真"
    )
    assert warm.completion_tokens > 0, (
        f"{model_id} completion_tokens=0（litellm 伪造 usage 指纹："
        "估算路径清零 completion）"
    )
    if warm.prompt_includes_cache:
        cached = warm.cache_read_input_tokens + warm.cache_creation_input_tokens
        assert cached <= warm.prompt_tokens, (
            f"{model_id} 尺度混血指纹：含缓存口径下 "
            f"read+creation({cached}) > prompt({warm.prompt_tokens})"
        )
    if not warm.cache_observable:
        pytest.skip(f"{model_id} 端点未回报缓存字段（不可观测），缓存断言跳过")
    assert warm.cache_read_input_tokens > 0, (
        f"{model_id} 第二次调用应命中缓存，实际 "
        f"read={warm.cache_read_input_tokens} prompt={warm.prompt_tokens}"
    )
    assert warm.cache_hit_rate >= _MIN_EXPECTED_HIT_RATE, (
        f"{model_id} 重复前缀命中率 {warm.cache_hit_rate:.2%} 低于 "
        f"{_MIN_EXPECTED_HIT_RATE:.0%}（缓存链路异常或 usage 尺度失真）"
    )
