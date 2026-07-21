"""生产形态探针：完全不传 max_tokens（与 cognee 实际请求一致）。"""

from __future__ import annotations

import asyncio
import json
import sqlite3

import litellm


def get_failing_memory() -> str:
    db = sqlite3.connect("config/memory/data/agent_memory.sqlite3")
    row = db.execute(
        "SELECT mem.content FROM cognee_sync_queue q JOIN memories mem ON mem.id=q.memory_id "
        "WHERE q.last_error LIKE '%max_tokens%' ORDER BY length(mem.content) DESC LIMIT 1",
    ).fetchone()
    db.close()
    return row[0] if row else ""


async def probe(label: str, content: str, **overrides) -> None:
    cfg = json.load(open("config/llm_clients.json"))
    provider = next(p for p in cfg["providers"] if p["id"] == "阿里")
    kwargs = dict(
        model="anthropic/qwen3.8-max-preview",
        api_key=provider["api_key"],
        api_base=provider["base_url"],
        temperature=0.0,
        messages=[
            {"role": "system", "content": "Extract entities and relationships as JSON with keys nodes and edges."},
            {"role": "user", "content": content},
        ],
        timeout=300,
        **overrides,
    )
    kwargs.pop("max_tokens", None)
    try:
        resp = await litellm.acompletion(**kwargs)
    except Exception as exc:
        print(f"{label}: 调用失败 {type(exc).__name__}: {str(exc)[:200]}")
        return
    choice = resp.choices[0]
    usage = resp.usage
    text = choice.message.content or ""
    thinking = getattr(choice.message, "reasoning_content", None) or ""
    print(
        f"{label}: finish={choice.finish_reason} "
        f"prompt={getattr(usage,'prompt_tokens','?')} completion={getattr(usage,'completion_tokens','?')} "
        f"正文={len(text)}字符 推理={len(thinking)}字符"
    )


async def main() -> None:
    content = get_failing_memory()
    print(f"失败记忆长度: {len(content)} 字符")
    # 生产形态：无 max_tokens + thinking budget 注入
    await probe(
        "无max_tokens+budget2048",
        content,
        extra_body={"thinking": {"type": "enabled", "budget_tokens": 2048}},
    )
    # 对照：显式 max_tokens=16384
    await probe(
        "max_tokens=16384+budget",
        content,
        max_tokens=16384,
        extra_body={"thinking": {"type": "enabled", "budget_tokens": 2048}},
    )


asyncio.run(main())
