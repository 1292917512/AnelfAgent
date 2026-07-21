"""一次性诊断：复现 cognee → qwen3.8 的 max_tokens 截断。

模拟 cognee GenericAPIAdapter 的确切调用形态（litellm anthropic 路由 +
json_mode 结构化抽取 prompt + max_tokens），对比 thinking 各形态。
"""

from __future__ import annotations

import asyncio
import json

import litellm

# cognee 图谱抽取风格的系统提示（长指令 + JSON schema 约束）
SYSTEM_PROMPT = """You are a top-tier algorithm designed for extracting information in structured formats to build a knowledge graph.
Extract the entities (nodes) and specify their type from the following text, also extract relationships between these nodes.
Respond with a JSON object containing "nodes" (list of {name, type, description}) and "edges" (list of {source, target, relationship, description}).
Only output valid JSON. Do not include any explanation."""

TEXT = "2026-07-18 主人在群里讨论了 send_message 工具的 int 类型 bug，涉及皇帝和管理员两个用户，最后通过绕过方案解决。"


async def probe(label: str, **overrides) -> None:
    cfg = json.load(open("config/llm_clients.json"))
    provider = next(p for p in cfg["providers"] if p["id"] == "阿里")
    kwargs = dict(
        model="anthropic/qwen3.8-max-preview",
        api_key=provider["api_key"],
        api_base=provider["base_url"],
        max_tokens=16384,
        temperature=0.0,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": TEXT},
        ],
        timeout=180,
        **overrides,
    )
    try:
        resp = await litellm.acompletion(**kwargs)
    except Exception as exc:
        print(f"{label}: 调用失败 {type(exc).__name__}: {str(exc)[:200]}")
        return
    choice = resp.choices[0]
    usage = resp.usage
    content = choice.message.content or ""
    reasoning = getattr(choice.message, "reasoning_content", None) or ""
    print(
        f"{label}: finish={choice.finish_reason} "
        f"completion_tokens={getattr(usage, 'completion_tokens', '?')} "
        f"正文={len(content)}字符 推理={len(reasoning)}字符"
    )
    if choice.finish_reason == "length":
        print(f"  ⚠️ 截断! 正文尾部: ...{content[-80:]!r}")


async def main() -> None:
    # 1. 当前 llm_bridge 注入的形态: thinking enabled + budget 2048
    await probe(
        "thinking+budget2048",
        extra_body={"thinking": {"type": "enabled", "budget_tokens": 2048}},
    )
    # 2. 完全不带 thinking（端点默认行为）
    await probe("无thinking参数   ")
    # 3. 显式关闭 thinking
    await probe(
        "thinking=disabled ",
        extra_body={"thinking": {"type": "disabled"}},
    )


asyncio.run(main())
