"""缓存优化回归测试：_layer 非破坏剥离 / 重复提示边界折叠 / 摘要截断 / 工具确定性排序。"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Dict, List

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

    def test_source_tags_stripped_and_preserved(self) -> None:
        """_source 来源标记：发送副本剥离，原消息保留（与 _layer 对称）。"""
        msgs = [
            {"role": "system", "content": "人设", "_layer": "stable"},
            {"role": "system", "content": "[系统] 超时提示",
             "_source": {"origin": "timeout_recovery"}},
        ]
        out = normalize_for_send(msgs)
        # 发送副本无 _source（LLM 不可见）
        assert all("_source" not in m for m in out)
        # 原消息 _source 保留（归因/审计依赖）
        assert msgs[1]["_source"] == {"origin": "timeout_recovery"}
        # 同时带 _layer 与 _source 的消息两者都被剥离
        mixed = [{"role": "system", "content": "x", "_layer": "stable",
                  "_source": {"origin": "compression"}}]
        out2 = normalize_for_send(mixed)
        assert "_layer" not in out2[0] and "_source" not in out2[0]
        assert mixed[0]["_layer"] == "stable"
        assert mixed[0]["_source"] == {"origin": "compression"}


def _compressor() -> ContextCompressor:
    config = SimpleNamespace(
        microcompact_chain_threshold=6,
        microcompact_keep_recent=2,
    )
    return ContextCompressor(mind=None, config=config)


class TestCollapseDupHints:
    """重复提示折叠只在既有重写断点之后搭便车，绝不独立制造缓存断点。"""

    @staticmethod
    def _call(cid: str, name: str = "read_file") -> Dict:
        return {
            "role": "assistant",
            "content": None,
            "tool_calls": [{"id": cid, "type": "function", "function": {"name": name, "arguments": "{}"}}],
        }

    def test_dups_survive_without_rewrite(self) -> None:
        """无结果清理/图片折叠时重复提示不动：为省百字符副本独立制造缓存
        断裂（其后整段全价重编码）是纯亏的交换，副本按缓存读价驻留。"""
        hint = "[系统提示] 工具结果仅你可见"
        chain = [
            {"role": "user", "content": "问"},
            {"role": "system", "content": hint},
            {"role": "assistant", "content": "答1"},
            {"role": "system", "content": hint},
            {"role": "assistant", "content": "答2"},
            {"role": "system", "content": hint},
            # 短结果且无配对 assistant tool_calls：本轮无任何重写
            {"role": "tool", "tool_call_id": "x", "content": "r"},
        ]
        assert _compressor().microcompact(chain) == 0
        assert [m["content"] for m in chain if m["role"] == "system"] == [hint] * 3

    def test_collapse_free_rides_after_result_rewrite(self) -> None:
        """结果清理建立断点后，断点之后的重复副本折叠（保留最新）；
        断点之前的副本原样保留——删除会把本轮缓存失效区向前扩大。"""
        hint = "[系统提示] 工具结果仅你可见"
        chain = [
            {"role": "system", "content": hint},  # 0 断点前：保留
            self._call("c0"),
            {"role": "tool", "tool_call_id": "c0", "content": "x" * 500},  # 2 → 断点
            {"role": "system", "content": hint},  # 3 断点后：折叠
            self._call("c1"),
            {"role": "tool", "tool_call_id": "c1", "content": "x" * 500},  # 5 → 占位符
            self._call("c2"),
            {"role": "tool", "tool_call_id": "c2", "content": "y" * 500},  # 7 窗口内：保留
            self._call("c3"),
            {"role": "tool", "tool_call_id": "c3", "content": "z" * 500},  # 9 窗口内：保留
            {"role": "system", "content": hint},  # 10 最新副本：保留
        ]
        n = _compressor().microcompact(chain)
        # n = 2 条结果清理 + 1 条搭便车副本
        assert n == 3
        hints = [m["content"] for m in chain if m["role"] == "system"]
        assert hints == [hint, hint]
        tools = [m["content"] for m in chain if m["role"] == "tool"]
        assert tools[0] == ContextCompressor._MICROCOMPACT_PLACEHOLDER
        assert tools[1] == ContextCompressor._MICROCOMPACT_PLACEHOLDER
        assert tools[2] == "y" * 500
        assert tools[3] == "z" * 500
        # tool_call 配对结构完整
        roles = [m["role"] for m in chain]
        assert roles.count("assistant") == roles.count("tool") == 4

    def test_collapse_free_rides_after_image_fold(self) -> None:
        """图片折叠同样建立断点：无结果可清时副本仍可在图片断点后折叠。"""
        hint = "[系统提示] 工具结果仅你可见"
        img_block = {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,XXX"}}
        chain: List[Dict] = [
            {
                "role": "user",
                "content": [  # 0 → 折叠＝断点
                    {"type": "text", "text": "[media_path:/tmp/a.jpg] 看图1"},
                    img_block,
                ],
            },
            {"role": "assistant", "content": "看到了"},
            {"role": "system", "content": hint},  # 2 断点后：折叠
            {
                "role": "user",
                "content": [  # 3 最新含图：不动
                    {"type": "text", "text": "[media_path:/tmp/b.jpg] 看图2"},
                    img_block,
                ],
            },
            {"role": "assistant", "content": "也看到了"},
            {"role": "system", "content": hint},  # 5 最新副本：保留
        ]
        n = _compressor().microcompact(chain)
        assert n == 2  # 1 图片块 + 1 副本
        hints = [m["content"] for m in chain if m["role"] == "system"]
        assert hints == [hint]

    def test_unique_system_hints_untouched(self) -> None:
        """互不重复的 system 提示即使落在断点之后也一条不删（折叠只针对重复副本）。"""
        chain = [
            {"role": "system", "content": "提示A"},
            self._call("c0"),
            {"role": "tool", "tool_call_id": "c0", "content": "r" * 300},
            {"role": "system", "content": "提示B"},
            self._call("c1"),
            {"role": "tool", "tool_call_id": "c1", "content": "r" * 300},
            self._call("c2"),
            {"role": "tool", "tool_call_id": "c2", "content": "r" * 300},
        ]
        n = _compressor().microcompact(chain)
        assert n == 1  # 仅结果清理
        hints = [m["content"] for m in chain if m["role"] == "system"]
        assert hints == ["提示A", "提示B"]

    def test_idempotent_on_rerun(self) -> None:
        """已清理过的链再次执行零动作（占位符不重复清理，副本不再增删）。"""
        chain = [
            self._call("c0"),
            {"role": "tool", "tool_call_id": "c0", "content": "x" * 500},
            {"role": "system", "content": "提示"},
            self._call("c1"),
            {"role": "tool", "tool_call_id": "c1", "content": "x" * 500},
            self._call("c2"),
            {"role": "tool", "tool_call_id": "c2", "content": "x" * 500},
            {"role": "system", "content": "提示"},
        ]
        c = _compressor()
        assert c.microcompact(chain) == 2  # 1 条结果（keep=2 窗口）+ 1 条副本
        assert c.microcompact(chain) == 0

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

    def test_scoped_tools_sort_after_shared(self) -> None:
        """作用域工具（频道能力/scope 激活分组）沉到共享核心之后：
        不同 scope 的 tools 数组共享最长公共头部（跨 scope 前缀缓存命中）。"""
        from agent.mind.tool_assembly import ToolAssembly

        ta = ToolAssembly.__new__(ToolAssembly)
        ta._tool_recall = {}
        scoped = {"send_message", "ban_user"}

        def key(n: str):
            return ta._tool_sort_key({"function": {"name": n}}, scoped)

        names = ["send_message", "zeta_shared", "ban_user", "alpha_shared", "end_reply"]
        ordered = sorted(names, key=key)
        # 共享桶：end_reply（核心优先 0）→ 名称序；作用域桶同规则（ban_user 名称在send_message 前）
        assert ordered == ["end_reply", "alpha_shared", "zeta_shared", "ban_user", "send_message"]

    def test_scope_subset_is_prefix_of_superset(self) -> None:
        """无频道工具的 scope 数组是有频道工具 scope 的前缀（缓存共享最大化）。"""
        from agent.mind.tool_assembly import ToolAssembly

        ta = ToolAssembly.__new__(ToolAssembly)
        ta._tool_recall = {}
        qq_scoped = {"send_message", "send_file"}

        def build(names, scoped):
            return sorted(names, key=lambda n: ta._tool_sort_key({"function": {"name": n}}, scoped))

        shared = ["end_reply", "recall", "memorize"]
        webui = build(shared, set())
        qq = build(shared + ["send_message", "send_file"], qq_scoped)
        assert qq[:len(webui)] == webui  # webui 数组是 qq 数组的严格前缀


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


class TestCacheKindBucketing:
    def test_kind_separated_stats(self) -> None:
        """辅助调用（reflect）与主对话（reply）分桶统计，互不污染命中率口径。"""
        from agent.llm.types import UsageInfo
        from agent.mind.cache_stats import CacheUsageTracker

        tracker = CacheUsageTracker()
        tracker.record(UsageInfo(prompt_tokens=100, cache_read_input_tokens=95), kind="reply")
        tracker.record(UsageInfo(prompt_tokens=50, cache_read_input_tokens=0), kind="reflect")

        reply = tracker.summary(kind="reply")
        assert reply["sample_count"] == 1
        assert reply["avg_cache_hit_rate"] == 0.95
        all_stats = tracker.summary()
        assert all_stats["sample_count"] == 2
        # last() 按用途过滤
        assert tracker.last(kind="reply")["cache_hit_rate"] == 0.95
        assert tracker.last(kind="reflect")["cache_hit_rate"] == 0.0
        assert tracker.last()["kind"] == "reflect"


class TestCacheRateAggregation:
    def test_avg_never_exceeds_full_hit(self) -> None:
        """Anthropic 口径（input 不含缓存）混窗时均值仍 ≤1。

        归一后的分母恒 ≥ read（不含口径 total = prompt+read+creation），
        单次命中率天然不超 1，聚合算术平均同样封顶。
        """
        from agent.llm.types import UsageInfo
        from agent.mind.cache_stats import CacheUsageTracker

        tracker = CacheUsageTracker()
        # Anthropic 记账：input_tokens 仅未缓存部分，read 远大于它
        tracker.record(
            UsageInfo(prompt_tokens=50, cache_read_input_tokens=200, prompt_includes_cache=False),
            kind="reflect",
        )
        tracker.record(UsageInfo(prompt_tokens=100, cache_read_input_tokens=50), kind="reply")

        stats = tracker.summary()
        assert stats["avg_cache_hit_rate"] == 0.65  # (0.8 + 0.5) / 2
        assert stats["avg_cache_hit_rate"] <= 1.0


class TestMcpSleepPolicy:
    def test_default_sleeps_all_mcp(self, monkeypatch) -> None:
        from core.config import ConfigManager
        from entities.mcp.config import _mcp_sleep_enabled

        ConfigManager.initialize()
        ConfigManager.set("mcp_sleep_excludes", "")
        assert _mcp_sleep_enabled("mind-map")
        ConfigManager.set("mcp_sleep_excludes", "git, excel")
        assert not _mcp_sleep_enabled("git")
        assert _mcp_sleep_enabled("mind-map")


class TestStickyActivation:
    def test_sticky_activation_no_expiry(self) -> None:
        """粘性模式（默认）：激活后 consume_round 不过期。"""
        from agent.mind.tool_activation import ToolActivationManager

        mgr = ToolActivationManager()
        mgr.activate("ssh", rounds=1, scope="s1")
        for _ in range(5):
            assert mgr.consume_round("s1") == []
        assert mgr.is_active("ssh", "s1")

    def test_non_sticky_expires(self, monkeypatch) -> None:
        """关闭粘性：恢复轮次过期。"""
        monkeypatch.setattr(
            "core.config.get_config_bool",
            lambda k, d=False: False if k == "tool_activation_sticky" else d,
        )
        from agent.mind.tool_activation import ToolActivationManager

        mgr = ToolActivationManager()
        mgr.activate("ssh", rounds=1, scope="s1")
        assert mgr.consume_round("s1") == ["ssh"]
        assert not mgr.is_active("ssh", "s1")


class TestStayAwakePolicy:
    def test_stay_awake_override(self, monkeypatch, tmp_path) -> None:
        """每服务 stay_awake 覆盖全局默认沉睡。"""
        import json

        import entities.mcp.config as mcp_config
        from core.config import ConfigManager

        cfg = tmp_path / "mcp_servers.json"
        cfg.write_text(json.dumps({"mcpServers": {"git": {"stay_awake": True}}}), encoding="utf-8")
        monkeypatch.setattr("core.path.ConfigPaths.MCP_SERVERS", str(cfg), raising=False)
        ConfigManager.initialize()
        ConfigManager.set("mcp_sleep_excludes", "")

        assert not mcp_config._mcp_sleep_enabled("git")   # stay_awake 覆盖
        assert mcp_config._mcp_sleep_enabled("mind-map")  # 未覆盖仍沉睡

        # 关闭覆盖后恢复沉睡
        cfg.write_text(json.dumps({"mcpServers": {"git": {"stay_awake": False}}}), encoding="utf-8")
        assert mcp_config._mcp_sleep_enabled("git")
