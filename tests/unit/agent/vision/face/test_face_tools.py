"""人脸 AI 工具面测试：face_update 字段编辑与 entity_scope 绑定语义。"""

from __future__ import annotations

import json

from agent.vision.face import tools as face_tools
from agent.vision.face.store import get_face_store


class TestFaceUpdate:
    async def test_update_name_and_role(self) -> None:
        store = get_face_store()
        person = await store.create_person(name="张三")
        out = json.loads(await face_tools.face_update(str(person["id"]), name="李四", role="同事"))
        assert out["person"]["name"] == "李四"
        assert out["person"]["role"] == "同事"
        assert out["updated_fields"] == ["name", "role"]

    async def test_update_entity_scope_binds(self) -> None:
        store = get_face_store()
        person = await store.create_person(name="张三")
        out = json.loads(await face_tools.face_update(
            str(person["id"]), entity_scope="user:qq:9"))
        assert out["person"]["entity_scope"] == "user:qq:9"
        assert "entity_scope" in out["updated_fields"]

    async def test_update_entity_scope_unbinds_on_empty(self) -> None:
        store = get_face_store()
        person = await store.create_person(name="张三", entity_scope="user:qq:9")
        out = json.loads(await face_tools.face_update(str(person["id"]), entity_scope=""))
        assert out["person"]["entity_scope"] == ""

    async def test_update_entity_scope_default_keeps(self) -> None:
        store = get_face_store()
        person = await store.create_person(name="张三", entity_scope="user:qq:9")
        out = json.loads(await face_tools.face_update(str(person["id"]), role="家人"))
        assert out["person"]["entity_scope"] == "user:qq:9"  # 未传不改

    async def test_update_invalid_scope_rejected(self) -> None:
        store = get_face_store()
        person = await store.create_person(name="张三")
        out = json.loads(await face_tools.face_update(str(person["id"]), entity_scope="bogus:x"))
        assert "error" in out

    async def test_update_no_fields_rejected(self) -> None:
        store = get_face_store()
        person = await store.create_person(name="张三")
        out = json.loads(await face_tools.face_update(str(person["id"])))
        assert "error" in out

    async def test_update_confirm_pending(self) -> None:
        store = get_face_store()
        person = await store.create_person(status="pending")
        out = json.loads(await face_tools.face_update(
            str(person["id"]), name="王五", confirm=True))
        assert out["person"]["status"] == "confirmed"
        assert out["person"]["person_key"].startswith("fc_")
