"""标签用途、关联工具及持久化失败的注册一致性。"""
from __future__ import annotations

import pytest

import core.tags as tags
from core.entity import EntityRegistry
from services import tag as service


@pytest.fixture
def catalog(monkeypatch: pytest.MonkeyPatch, tmp_path):
    monkeypatch.setattr(tags, "tag_list", [])
    monkeypatch.setattr(service, "_custom_tags_loaded", True)
    monkeypatch.setattr(service.ConfigPaths, "CUSTOM_TAGS", str(tmp_path / "tags.json"))
    return service.TagService()


def test_tool_only_tags_and_message_keys_are_distinguished(catalog) -> None:
    tags.Tag(tag_name="message_key", tag_name_desc="source")
    tags.Tag(tag_name="media:image", tag_name_desc="route")
    tags.Tag(tag_name="internal", visible_to_llm=False)
    EntityRegistry.register_tool(name="catalog_probe", func=lambda: None, group="catalog", tags=["media:image", "external_route"])
    try:
        result = {tag["name"]: tag for tag in catalog.list_unified_tags()}
        assert result["message_key"]["message_key"] is True
        assert result["media:image"]["sources"] == ["tool"]
        assert result["external_route"]["tools"] == ["catalog_probe"]
        assert result["external_route"]["tool_count"] == 1
        assert catalog.validate_tags(["external_route", "unregistered_route"]) == ["unregistered_route"]
        with pytest.raises(ValueError, match="内置标签"):
            catalog.create_tag("external_route", "must not shadow a registered route")
        assert result["internal"]["sources"] == ["tool"]
        assert "media:image" not in tags.get_tag_desc()
        assert "internal" not in tags.get_tag_desc()
    finally:
        EntityRegistry.unregister("catalog_probe")


def test_failed_save_does_not_register_tag(catalog, monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(_data):
        raise OSError("storage unavailable")
    monkeypatch.setattr(service, "_save_tags_file", fail)
    with pytest.raises(OSError):
        catalog.create_tag("new_tag", "description")
    assert not any(tag.tag_name == "new_tag" for tag in tags.tag_list)


def test_used_custom_tag_cannot_be_deleted(catalog) -> None:
    catalog.create_tag("used_tag", "description")
    EntityRegistry.register_tool(name="catalog_user", func=lambda: None, group="catalog", tags=["used_tag"])
    try:
        with pytest.raises(ValueError, match="仍被"):
            catalog.delete_tag("used_tag")
        assert "used_tag" in service._load_tags_file()
    finally:
        EntityRegistry.unregister("catalog_user")
    assert catalog.delete_tag("used_tag")
    assert "used_tag" not in service._load_tags_file()
