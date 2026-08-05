"""缓存优化回归测试：_layer 非破坏剥离 / 重复提示折叠 / 摘要截断 / 工具确定性排序。"""

from __future__ import annotations

from types import SimpleNamespace

from agent.mind.context_compressor import ContextCompressor
from agent.mind.message_schema import normalize_for_send
from agent.mind.tools.reply_finalize import _build_execution_summary


class TestNormalizeNonMutating:
    def test_layer_tags_survive_send(self) -> None:
        """normalize_for_send 不得破坏原消息的 _layer 标签（快照分类依赖）。"""
        msgs = [
            {"role": "system", "content": "人设", "_layer": "stable"},
            {"role": "user", "content": "你好", "_layer": "conversation"},
        ]
        out = normalize_for_send(msgs)
        # 发送副本无标签
        assert all("_layer" not in m for m in out)
        # 原消息标签保留（多轮会话后续快照仍能正确分类）
        assert msgs[0]["_layer"] == "stable"
        assert msgs[1]["_layer"] == "conversation"


def _compressor() -> ContextCompressor:
    config = SimpleNamespace(
        microcompact_chain_threshold=6,
        microcompact_keep_recent=2,
    )
    return ContextCompressor(mind=None, config=config)


class TestCollapseDupHints:
    def test_duplicate_system_hints_collapsed(self) -> None:
        """内容相同的 system 提示仅保留最新一条全文。"""
        hint = "[系统提示] 工具结果仅你可见"
        chain = [
            {"role": "user", "content": "问"},
            {"role": "system", "content": hint},
            {"role": "assistant", "content": "答1"},
            {"role": "system", "content": hint},
            {"role": "assistant", "content": "答2"},
            {"role": "system", "content": hint},
            {"role": "tool", "tool_call_id": "x", "content": "r"},
        ]
        n = _compressor().microcompact(chain)
        assert n >= 2
        full = [m for m in chain if m.get("content") == hint]
        collapsed = [m for m in chain if m.get("content") == "[重复的系统提示已折叠]"]
        assert len(full) == 1 and len(collapsed) == 2
        # 保留的是最新一条（索引最大）
        assert chain[5]["content"] == hint

    def test_short_chain_untouched(self) -> None:
        chain = [{"role": "system", "content": "x"}] * 2
        assert _compressor().microcompact(chain) == 0
        assert all(m["content"] == "x" for m in chain)


class TestExecutionSummaryCap:
    def test_long_args_truncated(self) -> None:
        chain = [
            {
                "role": "assistant",
                "tool_calls": [{
                    "id": "c1", "type": "function",
                    "function": {"name": "update_skill", "arguments": '{"content": "' + "长" * 1000 + '"}'},
                }],
            },
            {"role": "tool", "tool_call_id": "c1", "content": "ok"},
        ]
        summary = _build_execution_summary(chain, [])
        assert "长" * 1000 not in summary
        assert "…" in summary

    def test_many_tools_head_tail_kept(self) -> None:
        chain = []
        for i in range(40):
            chain.append({
                "role": "assistant",
                "tool_calls": [{
                    "id": f"c{i}", "type": "function",
                    "function": {"name": f"tool_{i}", "arguments": "{}"},
                }],
            })
            chain.append({
                "role": "tool", "tool_call_id": f"c{i}",
                "content": "结果" * 150,
            })
        summary = _build_execution_summary(chain, [])
        assert len(summary) < 4600
        assert "已省略" in summary
        assert "本轮共执行 40 次工具" in summary


class TestDeterministicToolOrder:
    def test_order_ignores_usage_counts(self) -> None:
        from agent.mind.tool_assembly import ToolAssembly

        ta = ToolAssembly.__new__(ToolAssembly)
        ta._tool_recall = {"zeta": 99, "alpha": 1}

        def key(n: str):
            return ta._tool_sort_key({"function": {"name": n}})

        names = ["zeta", "alpha", "beta"]
        # 确定性模式：纯按名称排序，与使用计数无关
        assert sorted(names, key=key) == ["alpha", "beta", "zeta"]


class TestClearStaleImageBlocks:
    def test_old_image_blocks_collapsed(self) -> None:
        """非最新的图片块折叠为文本占位，最新含图消息保留。"""
        img_block = {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,XXX"}}
        chain = [
            {"role": "user", "content": [
                {"type": "text", "text": "[media_path:/tmp/a.jpg] 看图1"},
                img_block,
            ]},
            {"role": "assistant", "content": "看到了"},
            {"role": "user", "content": [
                {"type": "text", "text": "[media_path:/tmp/b.jpg] 看图2"},
                img_block,
            ]},
            {"role": "assistant", "content": "也看到了"},
            {"role": "tool", "tool_call_id": "x", "content": "r"},
            {"role": "tool", "tool_call_id": "y", "content": "r2"},
        ]
        n = _compressor().microcompact(chain)
        assert n == 1
        first = chain[0]["content"]
        assert not any(b.get("type") == "image_url" for b in first)
        assert any("历史图片块已折叠" in b.get("text", "") for b in first)
        # media_path 文本块保留（可按需重读）
        assert any("media_path" in b.get("text", "") for b in first)
        # 最新含图消息不动
        assert any(b.get("type") == "image_url" for b in chain[2]["content"])


class TestStickyDynamicTools:
    def test_sticky_keeps_tag_activated(self) -> None:
        """粘性模式（默认）：空闲清理保留 tag 激活/动态发现，工具集跨会话稳定。"""
        from agent.mind.tool_assembly import ToolAssembly

        ta = ToolAssembly.__new__(ToolAssembly)
        ta._tag_activated_tools = {"recognize_image"}
        ta._discovered_tools = {"some_tool"}
        ta._tools_version = 5
        ta.clear_dynamic_tools()
        assert ta._tag_activated_tools == {"recognize_image"}
        assert ta._discovered_tools == {"some_tool"}
        assert ta._tools_version == 5  # 未递增 = 不触发重建

    def test_non_sticky_clears(self, monkeypatch) -> None:
        """关闭粘性：恢复每会话清空行为。"""
        from agent.mind.tool_assembly import ToolAssembly

        ta = ToolAssembly.__new__(ToolAssembly)
        ta._tag_activated_tools = {"recognize_image"}
        ta._discovered_tools = set()
        ta._tools_version = 5
        monkeypatch.setattr(
            "core.config.get_config_bool", lambda k, d=False: False,
        )
        ta.clear_dynamic_tools()
        assert not ta._tag_activated_tools
        assert ta._tools_version == 6


class TestMemoryStatusSplit:
    def test_strip_auto_status_block(self) -> None:
        from agent.memory.notes import AUTO_STATUS_BEGIN, AUTO_STATUS_END, _strip_auto_status_block

        text = f"# 当前状态\n\n{AUTO_STATUS_BEGIN}\n- 活跃记忆：297 条\n{AUTO_STATUS_END}\n\n## 主人信息\n内容"
        out = _strip_auto_status_block(text)
        assert "活跃记忆" not in out
        assert "主人信息" in out

    def test_memory_status_block_extracted(self, monkeypatch) -> None:
        import agent.memory.notes as notes

        monkeypatch.setattr(
            notes, "load_notes_content",
            lambda: f"# 当前状态\n{notes.AUTO_STATUS_BEGIN}\n- 活跃记忆：297 条\n{notes.AUTO_STATUS_END}\n其余",
        )
        block = notes.build_memory_status_block()
        assert "活跃记忆：297 条" in block
        assert "memory_stats" in block
        assert "其余" not in block


class TestSleepableCatalogStatic:
    def test_catalog_text_has_no_activation_state(self, monkeypatch) -> None:
        """沉睡分组目录文案不随激活状态变化（stable 层字节稳定）。"""
        from types import SimpleNamespace as SN

        import agent.mind.context_assembly as ca

        monkeypatch.setattr(
            ca.EntityRegistry, "get_entity_catalog",
            staticmethod(lambda: [{"group": "ssh", "description": "SSH 远程管理", "tool_count": 9}]),
        )
        monkeypatch.setattr(
            ca.EntityRegistry, "get_sleepable_groups",
            staticmethod(lambda: {"ssh": {"brief": "SSH 管理"}}),
        )
        monkeypatch.setattr(ca.EntityRegistry, "get_all", staticmethod(lambda: []))
        asm = ca.ContextAssembly(SN(), SN())
        msgs = asm.build_tool_system_prompt()
        text = "\n".join(str(m.get("content", "")) for m in msgs)
        assert "[可沉睡]" in text
        assert 'activate_tool_group(group="ssh")' in text
        # 文案中不再出现依赖激活状态的分支标记
        assert "[沉睡]" not in text
