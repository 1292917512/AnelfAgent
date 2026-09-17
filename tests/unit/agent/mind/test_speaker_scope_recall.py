"""声纹说话人实体标签的召回解析：[speaker_scope:] → related_scopes。

声纹绑定实体（音频库 user:qq:123 格式）经实时标注写入消息文本，
召回层解析为权威 entity_scope 格式（user_qq:123）参与画像/关系/记忆召回。
"""

from __future__ import annotations

import re
from types import SimpleNamespace

from agent.mind.recollection import (
    _extract_related_scopes,
    _extract_scopes_from_anything,
    _speaker_scopes,
)

_UID_RE = re.compile(r"\[(?:uid|at_uid):([^\]]+)\]")


def _mind_stub() -> SimpleNamespace:
    return SimpleNamespace(_RELATED_UID_RE=_UID_RE)


class TestSpeakerScopeParsing:
    def test_parses_bound_scopes(self) -> None:
        text = "[语音 说话人:张三 置信:0.87][speaker_scope:user:qq:456] 你好"
        assert _speaker_scopes(text) == ["user_qq:456"]

    def test_group_scope_converted(self) -> None:
        assert _speaker_scopes("[speaker_scope:group:qq:789]") == ["group_qq:789"]

    def test_agent_scope_skipped(self) -> None:
        # 自我指涉绑定不参与相关实体召回（自我画像恒注入）
        assert _speaker_scopes("[speaker_scope:agent:self]") == []

    def test_garbage_ignored(self) -> None:
        assert _speaker_scopes("[speaker_scope:bogus] [speaker_scope:]") == []


class TestRelatedScopes:
    def test_voice_tag_triggers_recall_in_private_chat(self) -> None:
        """私聊/通话场景：声纹标签即触发绑定实体的画像召回（不依赖群聊）。"""
        tail = [{"content": "[speaker_scope:user:qq:456] 在吗"}]
        scopes = _extract_related_scopes(_mind_stub(), tail, "user_webui:u1")
        assert "user_qq:456" in scopes

    def test_multiple_turns_dedupe(self) -> None:
        tail = [
            {"content": "[speaker_scope:user:qq:456] 第一句"},
            {"content": "[speaker_scope:user:qq:456] 第二句"},
            {"content": "[speaker_scope:user:tg:9] 另一人插话"},
        ]
        scopes = _extract_related_scopes(_mind_stub(), tail, "user_webui:u1")
        assert scopes == ["user_qq:456", "user_tg:9"]

    def test_primary_scope_never_duplicated(self) -> None:
        tail = [{"content": "[speaker_scope:user:qq:456] x"}]
        scopes = _extract_related_scopes(_mind_stub(), tail, "user_qq:456")
        assert scopes == []

    def test_group_uid_markers_still_work(self) -> None:
        """既有群聊 [uid:] 解析不受影响，两类标签可共存。"""
        tail = [{"content": "[uid:77][speaker_scope:user:qq:456] 群里的话"}]
        scopes = _extract_related_scopes(_mind_stub(), tail, "group_qq:100")
        assert "user_qq:77" in scopes
        assert "user_qq:456" in scopes


class TestAnythingScopes:
    def test_current_message_voice_tag(self) -> None:
        anything = SimpleNamespace(
            uid="1", adapter_key="webui",
            get_text_content=lambda: "[speaker_scope:user:qq:456] 语音内容")
        scopes = _extract_scopes_from_anything(
            _mind_stub(), anything, "user_webui:1")
        # 发送者本体与 primary_scope 相同（去重不重复出现）；声纹绑定实体入选
        assert scopes == ["user_qq:456"]
