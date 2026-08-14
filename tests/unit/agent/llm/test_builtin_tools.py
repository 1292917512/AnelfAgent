"""供应商内置工具（builtin_tools）合并与配置序列化的单元测试。

chat_completions 路径：web_search 转译为 enable_search（chat 端点 tools 仅收
function），其余内置类型跳过；responses 路径：{"type": ...} 声明透传进 tools。
"""

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


# ── chat_completions 路径 ────────────────────────────────────────────


def test_chat_web_search_translates_to_enable_search() -> None:
    client = _client(["web_search"])
    merged, patch = client._merge_builtin_tools([_fn_tool("memorize")])
    assert merged == [_fn_tool("memorize")]
    assert patch == {"enable_search": True}


def test_chat_web_search_search_options_passthrough() -> None:
    client = _client([{"type": "web_search", "search_options": {"forced_search": True}}])
    _, patch = client._merge_builtin_tools([_fn_tool("memorize")])
    assert patch == {"enable_search": True, "search_options": {"forced_search": True}}


def test_chat_drops_conflicting_local_function_schema() -> None:
    """与生效内置工具同名的本地 function 工具被剔除（内置优先），其余保留。"""
    client = _client(["web_search"])
    merged, _ = client._merge_builtin_tools([_fn_tool("web_search"), _fn_tool("web_fetch")])
    assert merged == [_fn_tool("web_fetch")]


def test_chat_unsupported_builtin_types_are_skipped() -> None:
    """chat 端点无对应能力的内置类型跳过（同名本地工具保留），只告警一次。"""
    client = _client(["web_extractor", "code_interpreter", "t2i_search", "i2i_search"])
    tools = [_fn_tool("web_extractor"), _fn_tool("memorize")]
    merged, patch = client._merge_builtin_tools(tools)
    assert merged == tools
    assert patch == {}
    assert client._builtin_chat_warned is True


def test_chat_merge_without_config_returns_same_object() -> None:
    client = _client([])
    tools = [_fn_tool("memorize")]
    merged, patch = client._merge_builtin_tools(tools)
    assert merged is tools
    assert patch == {}


def test_chat_merge_does_not_mutate_inputs() -> None:
    cfg_dict = {"type": "web_search", "search_options": {"forced_search": True}}
    client = _client([cfg_dict])
    tools = [_fn_tool("web_search")]
    merged, _ = client._merge_builtin_tools(tools)
    assert tools == [_fn_tool("web_search")]
    assert client.config.builtin_tools[0] == cfg_dict


def test_build_kwargs_injects_enable_search_not_raw_tool() -> None:
    """wire tools 不含裸 {"type": ...} 声明（chat 端点会 400），搜索经 extra_body 启用。"""
    client = _client(["web_search", "code_interpreter"])
    kwargs = client._build_kwargs(
        [{"role": "user", "content": "hello"}],
        tools=[_fn_tool("web_search"), _fn_tool("memorize")],
    )
    assert kwargs["tools"] == [_fn_tool("memorize")]
    assert kwargs["extra_body"]["enable_search"] is True


def test_build_kwargs_user_extra_body_wins_over_translation() -> None:
    """用户 extra_body 显式配置的 enable_search 覆盖内置转译产物。"""
    client = LLMClient(LLMClientConfig(
        name="qwen-max",
        model="qwen-max",
        api_type="dashscope",
        builtin_tools=["web_search"],
        extra_body={"enable_search": False},
    ))
    kwargs = client._build_kwargs(
        [{"role": "user", "content": "hello"}],
        tools=[_fn_tool("memorize")],
    )
    assert kwargs["extra_body"]["enable_search"] is False


def test_build_kwargs_without_tools_does_not_inject() -> None:
    """折叠/摘要等无 tools 的辅助调用不注入内置工具。"""
    client = _client(["web_search"])
    kwargs = client._build_kwargs([{"role": "user", "content": "hello"}])
    assert "tools" not in kwargs
    assert "enable_search" not in (kwargs.get("extra_body") or {})


# ── responses 路径 ───────────────────────────────────────────────────


def test_responses_merge_appends_builtin_declarations() -> None:
    client = _client(["web_search", "code_interpreter"])
    tools = [_fn_tool("memorize"), _fn_tool("web_fetch")]
    merged = client._merge_responses_builtin_tools(tools)
    assert merged[:2] == tools
    assert merged[2:] == [{"type": "web_search"}, {"type": "code_interpreter"}]


def test_responses_merge_drops_conflicting_local_function_schema() -> None:
    client = _client(["web_search"])
    merged = client._merge_responses_builtin_tools([_fn_tool("web_search"), _fn_tool("web_fetch")])
    assert merged == [_fn_tool("web_fetch"), {"type": "web_search"}]


def test_responses_merge_without_config_returns_same_object() -> None:
    client = _client([])
    tools = [_fn_tool("memorize")]
    assert client._merge_responses_builtin_tools(tools) is tools


# ── 配置序列化 ───────────────────────────────────────────────────────


def test_builtin_tools_serialization_round_trip() -> None:
    cfg = LLMClientConfig(name="m", builtin_tools=["web_search"])
    assert cfg.to_dict()["builtin_tools"] == ["web_search"]
    assert cfg.to_model_dict()["builtin_tools"] == ["web_search"]
    restored = LLMClientConfig.from_dict(cfg.to_dict())
    assert restored.builtin_tools == ["web_search"]
    # 空配置不写入模型层级序列化（避免配置文件冗余）
    assert "builtin_tools" not in LLMClientConfig(name="m").to_model_dict()
