"""供应商内置工具（builtin_tools）合并与配置序列化的单元测试。"""

from __future__ import annotations

import pytest

from agent.llm.config import LLMClientConfig
from agent.llm.llm_client import LLMClient


def _fn_tool(name: str) -> dict:
    return {
        "type": "function",
        "function": {"name": name, "description": "", "parameters": {"type": "object"}},
    }


def _client(builtin_tools: list) -> LLMClient:
    return LLMClient(LLMClientConfig(
        name="qwen-max",
        model="qwen-max",
        api_type="dashscope",
        builtin_tools=builtin_tools,
    ))


def test_config_rejects_invalid_builtin_tools() -> None:
    with pytest.raises(ValueError, match="builtin_tools"):
        LLMClientConfig(builtin_tools="web_search")
    with pytest.raises(ValueError, match="builtin_tools"):
        LLMClientConfig(builtin_tools=[""])
    with pytest.raises(ValueError, match="builtin_tools"):
        LLMClientConfig(builtin_tools=[{"name": "web_search"}])


def test_normalized_builtin_tools() -> None:
    cfg = LLMClientConfig(builtin_tools=[
        "web_search",
        {"type": "code_interpreter", "code_interpreter": {"timeout": 30}},
    ])
    normalized = cfg.normalized_builtin_tools()
    assert normalized == [
        {"type": "web_search"},
        {"type": "code_interpreter", "code_interpreter": {"timeout": 30}},
    ]
    # copy-on-write：归一化结果是副本，改写不影响共享配置
    normalized[1]["type"] = "mutated"
    assert cfg.builtin_tools[1]["type"] == "code_interpreter"


def test_merge_appends_builtin_declarations() -> None:
    client = _client(["web_search", "code_interpreter"])
    tools = [_fn_tool("memorize"), _fn_tool("web_fetch")]
    merged = client._merge_builtin_tools(tools)
    assert merged[:2] == tools
    assert merged[2:] == [{"type": "web_search"}, {"type": "code_interpreter"}]


def test_merge_drops_conflicting_local_function_schema() -> None:
    """与内置工具同名的本地 function 工具被剔除（内置优先），其余保留。"""
    client = _client(["web_search"])
    tools = [_fn_tool("web_search"), _fn_tool("web_fetch")]
    merged = client._merge_builtin_tools(tools)
    assert merged == [_fn_tool("web_fetch"), {"type": "web_search"}]


def test_merge_without_config_returns_same_object() -> None:
    client = _client([])
    tools = [_fn_tool("memorize")]
    assert client._merge_builtin_tools(tools) is tools


def test_merge_does_not_mutate_inputs() -> None:
    cfg_dict = {"type": "web_search", "web_search": {"enable_source": True}}
    client = _client([cfg_dict])
    tools = [_fn_tool("web_search")]
    merged = client._merge_builtin_tools(tools)
    assert tools == [_fn_tool("web_search")]
    assert client.config.builtin_tools[0] == cfg_dict
    assert merged[-1] is not cfg_dict


def test_build_kwargs_injects_builtin_tools() -> None:
    client = _client(["web_search"])
    kwargs = client._build_kwargs(
        [{"role": "user", "content": "hello"}],
        tools=[_fn_tool("web_search"), _fn_tool("memorize")],
    )
    names = [
        t["function"]["name"] if t["type"] == "function" else t["type"]
        for t in kwargs["tools"]
    ]
    assert names == ["memorize", "web_search"]


def test_build_kwargs_without_tools_does_not_inject() -> None:
    """折叠/摘要等无 tools 的辅助调用不注入内置工具。"""
    client = _client(["web_search"])
    kwargs = client._build_kwargs([{"role": "user", "content": "hello"}])
    assert "tools" not in kwargs


def test_builtin_tools_serialization_round_trip() -> None:
    cfg = LLMClientConfig(name="m", builtin_tools=["web_search"])
    assert cfg.to_dict()["builtin_tools"] == ["web_search"]
    assert cfg.to_model_dict()["builtin_tools"] == ["web_search"]
    restored = LLMClientConfig.from_dict(cfg.to_dict())
    assert restored.builtin_tools == ["web_search"]
    # 空配置不写入模型层级序列化（避免配置文件冗余）
    assert "builtin_tools" not in LLMClientConfig(name="m").to_model_dict()
