"""标签管理服务 — 统一标签注册表，所有标签（消息上下文 + 工具路由）的唯一来源。

自定义标签持久化到 config/tags.json（通过 ConfigPaths.CUSTOM_TAGS）。
工具只能引用标签系统中已存在的标签。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Set

from core.log import log
from core.path import ConfigPaths

_custom_tags_loaded = False


def _load_tags_file() -> Dict[str, Any]:
    """读取 config/tags.json，不存在则返回空字典。"""
    p = Path(ConfigPaths.CUSTOM_TAGS)
    if p.exists():
        try:
            return json.loads(p.read_text("utf-8"))
        except Exception as e:
            log(f"读取标签配置失败: {e}", "ERROR", tag="Tags")
    return {}


def _save_tags_file(data: Dict[str, Any]) -> None:
    """写入 config/tags.json。"""
    p = Path(ConfigPaths.CUSTOM_TAGS)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _ensure_custom_tags_loaded() -> None:
    global _custom_tags_loaded
    if _custom_tags_loaded:
        return
    _custom_tags_loaded = True
    TagService.load_custom_tags()


def get_all_tag_names() -> Set[str]:
    """返回标签系统中所有已注册标签名称（内置 + 自定义）。"""
    _ensure_custom_tags_loaded()
    from core.tags import tag_list
    return {t.tag_name for t in tag_list}


class TagService:

    def create_tag(self, name: str, description: str) -> Dict[str, Any]:
        """创建自定义标签，注册到标签系统并持久化到 config/tags.json。"""
        _ensure_custom_tags_loaded()
        from core.tags import tag_list, Tag
        import re

        if not name or not re.fullmatch(r"[a-z0-9_:\-]+", name):
            raise ValueError(f"标签名称 '{name}' 格式非法，只能包含小写字母、数字、下划线、连字符、冒号")

        existing_data = _load_tags_file()
        existing_names = {t.tag_name for t in tag_list}

        if name in existing_names and name not in existing_data:
            raise ValueError(f"'{name}' 是内置标签，无法创建同名自定义标签")
        if name in existing_data:
            raise ValueError(f"自定义标签 '{name}' 已存在")

        Tag(tag_name=name, tag_name_desc=description)

        existing_data[name] = {"description": description}
        _save_tags_file(existing_data)

        log(f"自定义标签已创建: {name}", tag="Tags")
        return {"name": name, "description": description, "builtin": False, "sources": ["custom"]}

    def delete_tag(self, name: str) -> bool:
        """删除自定义标签（内置标签不可删除）。返回是否成功删除。"""
        from core.tags import tag_list

        existing_data = _load_tags_file()

        if name not in existing_data:
            for t in tag_list:
                if t.tag_name == name:
                    raise ValueError(f"内置标签 '{name}' 不可删除")
            return False

        del existing_data[name]
        _save_tags_file(existing_data)

        for i, t in enumerate(tag_list):
            if t.tag_name == name:
                tag_list.pop(i)
                break

        log(f"自定义标签已删除: {name}", tag="Tags")
        return True

    def list_tool_tags(self) -> List[str]:
        """返回所有工具上注册的路由 tag（去重排序）。"""
        from core.entity import EntityRegistry, EntityType

        all_tags: set[str] = set()
        for e in EntityRegistry.get_by_type(EntityType.TOOL):
            all_tags.update(e.tags)
        return sorted(all_tags)

    def list_unified_tags(self) -> List[Dict[str, Any]]:
        """返回统一标签列表：标签系统中所有标签 + 工具使用情况标注。

        每个标签带 sources 字段标注其用途：
          - "message": 可作为 [key:value] 消息上下文标签
          - "tool":    被工具注册为路由标签
          - "custom":  用户自定义标签
        """
        _ensure_custom_tags_loaded()
        from core.tags import tag_list
        from core.entity import EntityRegistry, EntityType

        custom_tag_names: set[str] = set(_load_tags_file().keys())

        # 收集工具使用的标签名
        tool_used_tags: set[str] = set()
        for e in EntityRegistry.get_by_type(EntityType.TOOL):
            tool_used_tags.update(e.tags)

        result: List[Dict[str, Any]] = []
        for tag in tag_list:
            is_custom = tag.tag_name in custom_tag_names
            sources: List[str] = []
            if is_custom:
                sources.append("custom")
            else:
                sources.append("message")
            if tag.tag_name in tool_used_tags:
                sources.append("tool")

            result.append({
                "name": tag.tag_name,
                "description": tag.tag_name_desc,
                "builtin": not is_custom,
                "sources": sources,
            })

        def _sort(item: Dict[str, Any]) -> tuple:
            return (0 if item["builtin"] else 1, item["name"])

        result.sort(key=_sort)
        return result

    @staticmethod
    def validate_tags(tags: List[str]) -> List[str]:
        """校验标签列表，返回不在标签系统中的非法标签名。"""
        registered = get_all_tag_names()
        return [t for t in tags if t not in registered]

    @staticmethod
    def load_custom_tags() -> int:
        """从 config/tags.json 加载自定义标签到内存 tag_list（幂等，首次调用时触发）。"""
        from core.tags import tag_list, Tag

        data = _load_tags_file()
        if not data:
            return 0

        existing_names = {t.tag_name for t in tag_list}
        loaded = 0
        for name, meta in data.items():
            if name in existing_names:
                continue
            Tag(tag_name=name, tag_name_desc=meta.get("description", ""))
            loaded += 1

        if loaded:
            log(f"自定义标签已加载: {loaded} 个", tag="Tags")
        return loaded
