"""人脸识别实体标签的召回解析：[face_scope:] → related_scopes。

人脸绑定实体（人脸库 user:qq:123 格式）经 ingest 一次性历史通知写入对话，
召回层解析为权威 entity_scope 格式（user_qq:123）参与画像/关系/记忆召回，
与声纹 speaker_scope 共用同一解析路径。
"""

from __future__ import annotations

import re
from types import SimpleNamespace

from agent.mind.recollection import (
    _extract_related_scopes,
    _speaker_scopes,
)

_UID_RE = re.compile(r"\[(?:uid|at_uid):([^\]]+)\]")


def _mind_stub() -> SimpleNamespace:
    return SimpleNamespace(_RELATED_UID_RE=_UID_RE)


class TestFaceScopeParsing:
    def test_parses_bound_face_scope(self) -> None:
        text = "[人脸识别·一次性通知] 画面中出现: 张三 [face_scope:user:qq:456]"
        assert _speaker_scopes(text) == ["user_qq:456"]

    def test_group_scope_converted(self) -> None:
        assert _speaker_scopes("[face_scope:group:qq:789]") == ["group_qq:789"]

    def test_agent_scope_skipped(self) -> None:
        assert _speaker_scopes("[face_scope:agent:self]") == []

    def test_both_speaker_and_face(self) -> None:
        text = "[speaker_scope:user:qq:1][face_scope:user:tg:2]"
        assert _speaker_scopes(text) == ["user_qq:1", "user_tg:2"]

    def test_garbage_ignored(self) -> None:
        assert _speaker_scopes("[face_scope:bogus] [face_scope:]") == []


class TestRelatedScopes:
    def test_face_tag_triggers_recall_in_private_chat(self) -> None:
        tail = [{"content": "[face_scope:user:qq:456] 出现在画面里"}]
        scopes = _extract_related_scopes(_mind_stub(), tail, "user_webui:u1")
        assert "user_qq:456" in scopes

    def test_multiple_turns_dedupe(self) -> None:
        tail = [
            {"content": "[face_scope:user:qq:456] 第一帧"},
            {"content": "[face_scope:user:qq:456] 第二帧"},
            {"content": "[face_scope:user:tg:9] 另一人入镜"},
        ]
        scopes = _extract_related_scopes(_mind_stub(), tail, "user_webui:u1")
        assert scopes == ["user_qq:456", "user_tg:9"]
