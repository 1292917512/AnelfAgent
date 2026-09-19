"""WorkMemory 待处理信号与消息标签工具唤醒映射的单元测试。

信号（PendingSignal）：scope 的最新消息预览/路由/对话性质单一载体，
平行字典（previews/adapter_keys）已合并；to_me/kind 为元决策态势供数。
标签扫描（_scan_message_tags）：显式映射——仅媒体与频道标签承载工具路由，
任意 key/value 不再泛化激活（防用户可控值意外唤醒工具）。
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import List

from agent.mind.work_memory import PendingSignal, WorkMemory
from agent.utils.unique_queue import UniqueQueue


def _bare_work_memory() -> WorkMemory:
    """绕过构造依赖的最小 WorkMemory（仅初始化被测状态字段）。"""
    wm = WorkMemory.__new__(WorkMemory)
    wm._pending_signals = {}
    wm.pending_user = UniqueQueue()
    wm.pending_group = UniqueQueue()
    return wm


class TestPendingSignal:
    def test_upsert_preserves_fields(self) -> None:
        """set_adapter_key/set_message_preview 局部更新，不覆盖其余信号字段。"""
        wm = _bare_work_memory()
        wm.set_adapter_key("user_qq:1", "qq")
        wm.set_message_preview("user_qq:1", "在吗")
        signal = wm.get_pending_signal("user_qq:1")
        assert signal is not None
        assert (signal.preview, signal.adapter_key, signal.to_me, signal.kind) == ("在吗", "qq", False, "")

    def test_signal_defaults(self) -> None:
        wm = _bare_work_memory()
        assert wm.get_pending_signal("user_qq:none") is None
        assert wm.get_adapter_key("user_qq:none") == ""

    def test_known_scopes_requires_adapter(self) -> None:
        """仅登记了 adapter 路由的 scope 进 known_scopes（防不可路由解析命中）。"""
        wm = _bare_work_memory()
        wm.set_message_preview("user_qq:no_route", "x")
        wm.set_adapter_key("user_qq:routed", "qq")
        scopes = wm.known_scopes()
        assert "user_qq:routed" in scopes
        assert "user_qq:no_route" not in scopes

    def test_previews_snapshot_shape(self) -> None:
        wm = _bare_work_memory()
        wm._pending_signals["user_qq:1"] = PendingSignal(preview="hi", adapter_key="qq")
        assert wm.get_pending_message_previews() == {"user_qq:1": ("hi", "qq")}


class TestScanMessageTags:
    def _scan(self, content: str) -> List[str]:
        wm = _bare_work_memory()
        activated: List[str] = []
        wm.tool_assembly = SimpleNamespace(activate_by_tag=activated.append)
        wm._scan_message_tags(content)
        return activated

    def test_media_and_channel_map_to_tools(self) -> None:
        activated = self._scan(
            "[time:2026年09月19日][channel:qq][media_type:image][media_path:/tmp/a.png]"
        )
        assert sorted(activated) == ["media:image", "qq"]

    def test_channel_value_lowered(self) -> None:
        """频道 id 大小写不定，工具 tag 恒小写——激活前归一。"""
        assert self._scan("[channel:QQ]") == ["qq"]

    def test_media_file_takes_first_segment(self) -> None:
        assert self._scan("[media_file:voice:/tmp/a.silk]") == ["media:voice"]

    def test_meta_tags_do_not_wake_tools(self) -> None:
        """uid/name/kind 等元数据与用户可控值不承载工具路由。"""
        assert self._scan("[uid:123][name:张三][kind:notification][at_uid:all] 正文") == []

    def test_missing_assembly_is_noop(self) -> None:
        wm = _bare_work_memory()
        wm.tool_assembly = None
        wm._scan_message_tags("[channel:qq]")  # 不应抛异常
