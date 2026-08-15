"""MCP 工具面细节优化（结果转换/热同步/超时/名长护栏/重试预算）单元测试。

覆盖 entities.mcp.bridge 第四轮优化的五组行为：
1. CallToolResult 内容块分派（图片落盘 + _multimodal 约定、audio/resource 占位、
   structuredContent 兜底）；
2. ToolListChangedNotification 拦截 → 防抖 → 增量重同步；
3. 注册工具携带 call_timeout（修复 60s 全局默认先于 bridge 超时掐断的错配）；
4. 参数 schema 保真（anyOf 解引用 / default / items）与注册名整形（64 字符上限）；
5. 重连重试预算的稳定窗口复位。
"""

from __future__ import annotations

import asyncio
import base64
import json
from types import SimpleNamespace
from typing import Generator, List

import pytest

import entities.mcp.bridge as bridge_mod
from core.entity import EntityRegistry
from entities.mcp.bridge import MCPBridge, _RetryBudget


@pytest.fixture()
def bridge() -> Generator[MCPBridge, None, None]:
    instance = MCPBridge()
    yield instance
    instance.shutdown()
    EntityRegistry.unregister_group("mcp:test-srv")


@pytest.fixture()
def upload_dir(monkeypatch: pytest.MonkeyPatch, tmp_path) -> str:
    """把 MCP 图片落盘目录重定向到临时目录。"""
    target = str(tmp_path / "uploads")
    monkeypatch.setattr(bridge_mod, "ConfigPaths", SimpleNamespace(UPLOAD_DIR=target))
    return target


# ------------------------------------------------------------------
# 1. CallToolResult 内容块分派
# ------------------------------------------------------------------

def _png_bytes() -> bytes:
    # 1x1 透明 PNG
    return base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk"
        "YPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
    )


def _result(*blocks, structured=None, is_error=False) -> SimpleNamespace:
    return SimpleNamespace(
        content=list(blocks), structuredContent=structured, isError=is_error,
    )


def _img_block(data_b64: str = "", mime: str = "image/png") -> SimpleNamespace:
    if not data_b64:
        data_b64 = base64.b64encode(_png_bytes()).decode()
    return SimpleNamespace(type="image", data=data_b64, mimeType=mime)


def _text_block(text: str) -> SimpleNamespace:
    return SimpleNamespace(type="text", text=text)


@pytest.mark.asyncio
async def test_image_saved_and_multimodal(bridge: MCPBridge, upload_dir: str) -> None:
    """image 块应落盘并经 _multimodal 约定返回，base64 原文不进结果。"""
    out = await bridge._render_call_result(_result(_text_block("截图完成"), _img_block()))

    parsed = json.loads(out)
    assert parsed["_multimodal"] is True
    assert parsed["text"].startswith("截图完成")
    assert len(parsed["images"]) == 1
    with open(parsed["images"][0], "rb") as f:
        assert f.read() == _png_bytes()
    # base64 原文绝不倾倒进工具结果（此前 str(item) 的 pydantic repr 行为）
    assert base64.b64encode(_png_bytes()).decode() not in out


@pytest.mark.asyncio
async def test_text_blocks_joined_plain(bridge: MCPBridge) -> None:
    """纯文本块按序拼接为普通文本，不包 JSON（与旧版行为一致）。"""
    out = await bridge._render_call_result(_result(_text_block("第一段"), _text_block("第二段")))
    assert out == "第一段\n第二段"


@pytest.mark.asyncio
async def test_audio_and_resource_placeholders(bridge: MCPBridge) -> None:
    """audio/resource_link/embedded resource 以短占位呈现，不灌二进制。"""
    audio = SimpleNamespace(type="audio", data="AAAA" * 1024, mimeType="audio/wav")
    link = SimpleNamespace(type="resource_link", uri="file:///tmp/report.md")
    embedded_text = SimpleNamespace(
        type="resource",
        resource=SimpleNamespace(uri="file:///tmp/note.txt", text="资源正文", blob=None),
    )
    embedded_blob = SimpleNamespace(
        type="resource",
        resource=SimpleNamespace(uri="file:///tmp/data.bin", text=None, blob="QUJD" * 512),
    )

    out = await bridge._render_call_result(
        _result(_text_block("ok"), audio, link, embedded_text, embedded_blob),
    )

    assert "audio/wav" in out and "已丢弃" in out
    assert "file:///tmp/report.md" in out
    assert "资源正文" in out  # 文本资源内容保留
    assert "data.bin" in out and "已丢弃" in out
    assert "_multimodal" not in out


@pytest.mark.asyncio
async def test_structured_content_fallback(bridge: MCPBridge) -> None:
    """无任何文本时 structuredContent 兜底输出 JSON。"""
    out = await bridge._render_call_result(
        _result(structured={"rows": 3, "ok": True}),
    )
    assert json.loads(out) == {"rows": 3, "ok": True}


@pytest.mark.asyncio
async def test_is_error_returns_tool_error(bridge: MCPBridge) -> None:
    """远端 isError 标记恢复为结构化错误信号（cause=internal）。"""

    class _ErrorSession:
        async def call_tool(self, name: str, arguments: dict) -> SimpleNamespace:
            return _result(_text_block("参数非法"), is_error=True)

    bridge._sessions["test-srv"] = _ErrorSession()
    bridge._find_server_config = lambda name: SimpleNamespace(call_timeout=5.0)  # type: ignore[method-assign]

    out = await bridge._do_call_tool("test-srv", "bad_tool", {})
    parsed = json.loads(out)
    assert parsed["error"].startswith("MCP 工具")
    assert parsed["cause"] == "internal"
    assert parsed["retryable"] is False
    assert "参数非法" in parsed["error"]


@pytest.mark.asyncio
async def test_image_passthrough_disabled(
        bridge: MCPBridge, upload_dir: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """mcp_image_passthrough 关闭时仅保留丢弃说明，不落盘不注入。"""
    monkeypatch.setattr(
        "core.config.get_config_bool", lambda k, d=False: False if k == "mcp_image_passthrough" else d,
    )
    out = await bridge._render_call_result(_result(_img_block()))

    assert "_multimodal" not in out
    assert "未注入" in out
    import os
    assert not os.path.exists(upload_dir)


@pytest.mark.asyncio
async def test_image_count_cap(bridge: MCPBridge, upload_dir: str) -> None:
    """单次结果最多注入 4 张图片，超出的以数量说明。"""
    blocks = [_img_block() for _ in range(6)]
    out = await bridge._render_call_result(_result(*blocks))

    parsed = json.loads(out)
    assert len(parsed["images"]) == 4
    assert "2 张图片未注入" in parsed["text"]


@pytest.mark.asyncio
async def test_image_corrupt_data_placeholder(bridge: MCPBridge, upload_dir: str) -> None:
    """解码失败的图片以占位说明，不抛异常。"""
    out = await bridge._render_call_result(
        _result(_img_block(data_b64="!!!非法base64!!!")),
    )
    assert "image/png" in out and "解码失败" in out


# ------------------------------------------------------------------
# 2. 工具列表变更热同步
# ------------------------------------------------------------------

class _FakeListSession:
    """按脚本返回 tools/list 结果的假会话。"""

    def __init__(self, tools: List[SimpleNamespace]) -> None:
        self._tools = tools

    async def list_tools(self) -> SimpleNamespace:
        return SimpleNamespace(tools=list(self._tools))


@pytest.mark.asyncio
async def test_sync_adds_and_removes_tools(bridge: MCPBridge) -> None:
    """增量同步：注销已删工具，注册新增工具，保留未变工具。"""
    srv = SimpleNamespace(
        name="test-srv", transport="stdio", command="uvx", url="",
        call_timeout=30.0,
    )
    await bridge._register_server_tools(srv, _FakeListSession([
        SimpleNamespace(name="old_tool", description="旧的", inputSchema={}),
        SimpleNamespace(name="keep_tool", description="保留", inputSchema={}),
    ]))
    bridge._sessions["test-srv"] = _FakeListSession([
        SimpleNamespace(name="keep_tool", description="保留", inputSchema={}),
        SimpleNamespace(name="new_tool", description="新增", inputSchema={}),
    ])

    await bridge._sync_server_tools("test-srv")

    assert EntityRegistry.get("old_tool") is None
    assert "old_tool" not in bridge._tool_server_map
    assert EntityRegistry.get("keep_tool") is not None
    assert EntityRegistry.get("new_tool") is not None
    assert bridge._tool_server_map["new_tool"] == "test-srv"
    # 实体工具清单与分组目录同步刷新
    mcp_entity = EntityRegistry.get("mcp:test-srv")
    assert mcp_entity is not None
    assert sorted(mcp_entity.meta["tools"]) == ["keep_tool", "new_tool"]
    assert "new_tool" in EntityRegistry._group_descriptions["mcp:test-srv"]
    assert "test-srv" not in bridge._sync_pending


@pytest.mark.asyncio
async def test_sync_skips_when_no_session(bridge: MCPBridge) -> None:
    """会话不存在时同步为 no-op 且清掉防抖标记。"""
    bridge._sync_pending.add("test-srv")
    await bridge._sync_server_tools("test-srv")
    assert "test-srv" not in bridge._sync_pending


def _tool_list_changed_note() -> object:
    from mcp import types
    return types.ServerNotification(root=types.ToolListChangedNotification())


@pytest.mark.asyncio
async def test_message_handler_debounces_to_sync(
        bridge: MCPBridge, monkeypatch: pytest.MonkeyPatch) -> None:
    """tools/list_changed 通知经防抖触发一次同步；其他消息零干预。"""
    monkeypatch.setattr(bridge_mod, "_TOOL_SYNC_DEBOUNCE_SEC", 0.05)
    called: List[str] = []

    async def fake_sync(name: str) -> None:
        called.append(name)
        bridge._sync_pending.discard(name)  # 复刻真实方法的 finally 语义

    monkeypatch.setattr(bridge, "_sync_server_tools", fake_sync)
    handler = bridge._make_message_handler("test-srv")

    await handler(_tool_list_changed_note())
    await handler(SimpleNamespace(type="text", text="普通消息"))  # 防抖窗口内合并
    await asyncio.sleep(0.2)

    assert called == ["test-srv"]
    assert "test-srv" not in bridge._sync_pending


@pytest.mark.asyncio
async def test_message_handler_config_disabled(
        bridge: MCPBridge, monkeypatch: pytest.MonkeyPatch) -> None:
    """mcp_tool_list_sync 关闭时通知不触发同步。"""
    monkeypatch.setattr(bridge_mod, "_TOOL_SYNC_DEBOUNCE_SEC", 0.05)
    monkeypatch.setattr(
        "core.config.get_config_bool", lambda k, d=False: False if k == "mcp_tool_list_sync" else d,
    )
    called: List[str] = []

    async def fake_sync(name: str) -> None:
        called.append(name)

    monkeypatch.setattr(bridge, "_sync_server_tools", fake_sync)
    handler = bridge._make_message_handler("test-srv")

    await handler(_tool_list_changed_note())
    await asyncio.sleep(0.15)

    assert called == []
    assert "test-srv" not in bridge._sync_pending


def test_is_tool_list_changed_rejects_others(bridge: MCPBridge) -> None:
    """非通知对象不误判（duck 兜底）。"""
    assert bridge._is_tool_list_changed(SimpleNotificationStub()) is False
    assert bridge._is_tool_list_changed(_tool_list_changed_note()) is True


class SimpleNotificationStub:
    pass


# ------------------------------------------------------------------
# 3. 注册工具携带 call_timeout
# ------------------------------------------------------------------

def test_register_entries_carries_timeout(bridge: MCPBridge) -> None:
    """显式 call_timeout 透传为工具执行超时 meta。"""
    tools = [SimpleNamespace(name="slow_query", description="慢查询", inputSchema={})]
    bridge._register_tool_entries("test-srv", tools, call_timeout=123)

    entity = EntityRegistry.get("slow_query")
    assert entity is not None
    assert entity.meta["timeout"] == 123.0


def test_register_entries_without_timeout_keeps_default(bridge: MCPBridge) -> None:
    """不传 call_timeout 时不写 timeout meta（保持全局默认行为）。"""
    tools = [SimpleNamespace(name="fast_tool", description="快", inputSchema={})]
    bridge._register_tool_entries("test-srv", tools)

    entity = EntityRegistry.get("fast_tool")
    assert entity is not None
    assert "timeout" not in entity.meta


def test_register_server_tools_passes_call_timeout(bridge: MCPBridge) -> None:
    """_register_server_tools 从 server 配置取 call_timeout 下发。"""
    srv = SimpleNamespace(
        name="test-srv", transport="stdio", command="uvx", url="",
        call_timeout=77,
    )
    asyncio.run(bridge._register_server_tools(srv, _FakeListSession([
        SimpleNamespace(name="tool_a", description="a", inputSchema={}),
    ])))

    entity = EntityRegistry.get("tool_a")
    assert entity is not None
    assert entity.meta["timeout"] == 77.0
    EntityRegistry.unregister("mcp:test-srv")


# ------------------------------------------------------------------
# 4. 参数 schema 保真与注册名整形
# ------------------------------------------------------------------

def test_parse_param_anyof_default_items() -> None:
    """anyOf 可选参数解引用取非 null 分支；default/items 进 schema_extra。"""
    tool = SimpleNamespace(
        name="t",
        description="",
        inputSchema={
            "type": "object",
            "properties": {
                "limit": {
                    "description": "数量",
                    "anyOf": [{"type": "integer"}, {"type": "null"}],
                    "default": 10,
                },
                "tags": {"type": "array", "items": {"type": "string"}},
                "plain": {"type": "string"},
            },
            "required": ["plain"],
        },
    )
    _name, params = MCPBridge._parse_mcp_tool(tool)
    by_name = {p.name: p for p in params}

    assert by_name["limit"].type == "integer"
    assert by_name["limit"].required is False
    assert by_name["limit"].schema_extra == {"default": 10}
    assert by_name["tags"].schema_extra == {"items": {"type": "string"}}
    assert by_name["plain"].schema_extra is None
    assert by_name["plain"].type == "string"


def test_parse_param_non_dict_schema_safe() -> None:
    """properties 值非 dict 时安全跳过（不抛异常）。"""
    tool = SimpleNamespace(
        name="t", description="", inputSchema={"properties": {"bad": "not-a-dict"}},
    )
    _name, params = MCPBridge._parse_mcp_tool(tool)
    assert len(params) == 1
    assert params[0].name == "bad"
    assert params[0].type == "string"


def test_tool_name_length_guard(bridge: MCPBridge) -> None:
    """超长注册名截断 + 短哈希后缀，原始名映射兜底。"""
    long_name = "a" * 80
    tools = [SimpleNamespace(name=long_name, description="", inputSchema={})]
    registered = bridge._register_tool_entries("test-srv", tools)

    reg = registered[0]
    assert len(reg) == 64
    assert reg != long_name
    assert bridge._tool_original_names[reg] == long_name
    assert EntityRegistry.get(reg) is not None


def test_tool_name_illegal_chars_sanitized(bridge: MCPBridge) -> None:
    """非法字符（如 server 名带点号）替换为下划线，原始名兜底。"""
    tools = [SimpleNamespace(name="query.v2", description="", inputSchema={})]
    # 先占用 query_v2 使冲突前缀路径也参与整形
    EntityRegistry.register_tool(
        name="query_v2", func=lambda **kw: "", description="占位",
        group="test", source="internal",
    )
    try:
        registered = bridge._register_tool_entries("my.server", tools)
        reg = registered[0]
        assert reg == "my_server__query_v2"
        assert bridge._tool_original_names[reg] == "query.v2"
    finally:
        EntityRegistry.unregister("query_v2")


# ------------------------------------------------------------------
# 5. 结构化错误与重试预算
# ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_call_tool_unknown_structured(bridge: MCPBridge) -> None:
    """未命中 server 的调用返回结构化 not_found 错误。"""
    out = await bridge.call_tool("ghost_tool", {})
    parsed = json.loads(out)
    assert parsed["cause"] == "not_found"
    assert parsed["retryable"] is False
    assert "list_mcp_servers" in parsed["hint"]


@pytest.mark.asyncio
async def test_do_call_timeout_structured(bridge: MCPBridge) -> None:
    """bridge 层超时返回 cause=timeout + code=TOOL_TIMEOUT。"""

    class _SlowSession:
        async def call_tool(self, name: str, arguments: dict) -> SimpleNamespace:
            await asyncio.sleep(0.3)
            return SimpleNamespace(content=[], isError=False)

    bridge._sessions["test-srv"] = _SlowSession()
    bridge._find_server_config = lambda name: SimpleNamespace(call_timeout=0.05)  # type: ignore[method-assign]

    out = await bridge._do_call_tool("test-srv", "slow", {})
    parsed = json.loads(out)
    assert parsed["cause"] == "timeout"
    assert parsed["code"] == "TOOL_TIMEOUT"
    assert parsed["retryable"] is True


def test_retry_budget_backoff_sequence() -> None:
    """连续失败退避 1/2/4/8/16s，5 次后耗尽（与旧 for 循环一致）。"""
    budget = _RetryBudget(5, 300.0)
    delays = [budget.record_failure() for _ in range(5)]
    assert delays == [1, 2, 4, 8, 16]
    assert budget.exhausted is True


def test_retry_budget_reset_after_stability() -> None:
    """稳定运行超过窗口后预算复位：本次失败重新从最短退避计起。"""
    budget = _RetryBudget(5, 300.0)
    for _ in range(3):
        budget.record_failure()
    assert budget.attempt == 3

    delay = budget.record_failure(stable_seconds=400.0)
    assert delay == 1.0
    assert budget.attempt == 1
    assert budget.exhausted is False


def test_retry_budget_no_reset_below_window() -> None:
    """稳定时长不足窗口时不复位（短暂连接抖动照常累计）。"""
    budget = _RetryBudget(5, 300.0)
    budget.record_failure(stable_seconds=100.0)
    assert budget.attempt == 1
    budget.record_failure(stable_seconds=299.9)
    assert budget.attempt == 2


# ------------------------------------------------------------------
# 6. 热同步 → AI 装配可见性（端到端链路）
# ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_sync_visible_to_tool_assembly(bridge: MCPBridge) -> None:
    """热同步增删后，激活组的工具装配实时反映（think_loop 重建即所见）。

    链路：_sync_server_tools → EntityRegistry.bump_version（think_loop 版本
    元组含 registry.version，见 think_loop 工具集版本检查）→ 重新装配时
    get_tool_schemas_by_group 实时查注册表 → 新工具进 schema、已删工具消失。
    """
    from agent.mind.tool_activation import tool_activation
    from agent.mind.tool_assembly import ToolAssembly

    async def _schemas() -> List[str]:
        assembly = ToolAssembly()
        schemas = await assembly.get_active_tool_schemas(scope="s-sync")
        return [s["function"]["name"] for s in schemas]

    srv = SimpleNamespace(
        name="test-srv", transport="stdio", command="uvx", url="",
        call_timeout=30.0,
    )
    await bridge._register_server_tools(srv, _FakeListSession([
        SimpleNamespace(name="v1_tool", description="v1", inputSchema={}),
    ]))
    tool_activation.activate("mcp:test-srv", scope="s-sync")
    try:
        assert "v1_tool" in await _schemas()

        # server 端工具变更：v1_tool 移除、v2_tool 新增
        bridge._sessions["test-srv"] = _FakeListSession([
            SimpleNamespace(name="v2_tool", description="v2", inputSchema={}),
        ])
        version_before = EntityRegistry.version()
        await bridge._sync_server_tools("test-srv")
        assert EntityRegistry.version() > version_before  # 触发 think_loop 重建的信号

        names = await _schemas()
        assert "v1_tool" not in names
        assert "v2_tool" in names
    finally:
        tool_activation.clear_scope("s-sync")
        EntityRegistry.unregister("mcp:test-srv")
