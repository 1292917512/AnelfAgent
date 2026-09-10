"""实体前端 i18n 一致性校验 — _registry 与后端注册对齐 + zh/en 键奇偶。

实体组名翻译自持于 entities/<name>/panels/locales/{zh,en}.json 的保留键
``_registry``（groups → 工具页分组名 / configSections → 配置中心分组名），
启动时经 entity-plugin-locales.ts eager 注册。本测试机械防止三类漂移：

1. zh/en 双语键集合必须一一对应（缺译即红）；
2. _registry 声明的 key 必须真实存在于该实体后端注册（防写错/过期键）；
3. 实体后端静态注册的 group 必须被 _registry.groups 覆盖（防新组漏译）。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, Set

PROJECT_ROOT = Path(__file__).resolve().parents[3]
ENTITIES_DIR = PROJECT_ROOT / "entities"

_GROUP_RE = re.compile(r'\bgroup="([^"]+)"')
_ENTITY_RE = re.compile(r'\bentity\("([^"]+)"')
_SECTION_RE = re.compile(r'"entity/([a-z_]+)"')

# 非工具展示组的静态注册（实体元数据分组等），豁免 _registry.groups 覆盖要求
_GROUP_EXEMPTIONS = {"mcp"}


def _entity_dirs() -> list[Path]:
    return sorted(
        d for d in ENTITIES_DIR.iterdir()
        if d.is_dir() and not d.name.startswith(("_", "."))
    )


def _backend_keys(entity_dir: Path) -> tuple[Set[str], Set[str]]:
    """实体非测试 Python 代码中的静态 group 注册与 entity/* 配置组。"""
    groups: Set[str] = set()
    sections: Set[str] = set()
    for py in entity_dir.rglob("*.py"):
        if "tests" in py.relative_to(entity_dir).parts:
            continue
        text = py.read_text(encoding="utf-8")
        groups.update(_GROUP_RE.findall(text))
        groups.update(_ENTITY_RE.findall(text))
        sections.update(_SECTION_RE.findall(text))
    return groups, sections


def _load_locales(entity_dir: Path) -> Dict[str, Any] | None:
    """读取实体 locale 双语文档（无 locales 目录返回 None）。"""
    locales_dir = entity_dir / "panels" / "locales"
    if not locales_dir.is_dir():
        return None
    result: Dict[str, Any] = {}
    for lang in ("zh", "en"):
        path = locales_dir / f"{lang}.json"
        if path.exists():
            result[lang] = json.loads(path.read_text(encoding="utf-8"))
    return result or None


def _deep_keys(data: Any, prefix: str = "") -> Set[str]:
    """嵌套字典的叶子键路径集合。"""
    if not isinstance(data, dict):
        return {prefix}
    keys: Set[str] = set()
    for key, value in data.items():
        keys.update(_deep_keys(value, f"{prefix}.{key}" if prefix else key))
    return keys


class TestLocaleKeyParity:
    """每个实体 locale 的 zh/en 键集合必须一致。"""

    def test_all_entities(self) -> None:
        problems: list[str] = []
        for entity_dir in _entity_dirs():
            locales = _load_locales(entity_dir)
            if locales is None:
                continue
            if "zh" not in locales or "en" not in locales:
                problems.append(f"{entity_dir.name}: 缺少 zh 或 en locale 文件")
                continue
            zh_keys = _deep_keys(locales["zh"])
            en_keys = _deep_keys(locales["en"])
            if zh_keys != en_keys:
                only_zh = sorted(zh_keys - en_keys)[:5]
                only_en = sorted(en_keys - zh_keys)[:5]
                problems.append(
                    f"{entity_dir.name}: 键不一致（仅 zh: {only_zh}；仅 en: {only_en}）"
                )
        assert not problems, "\n".join(problems)


class TestRegistryConsistency:
    """_registry 声明与实体后端注册双向对齐。"""

    def test_registry_keys_exist_in_backend(self) -> None:
        """_registry 的每个 key 必须是该实体真实注册的组（防过期/写错）。"""
        problems: list[str] = []
        for entity_dir in _entity_dirs():
            locales = _load_locales(entity_dir)
            if locales is None:
                continue
            registry = locales.get("zh", {}).get("_registry", {})
            groups, sections = _backend_keys(entity_dir)
            for key in registry.get("groups", {}):
                if key not in groups:
                    problems.append(
                        f"{entity_dir.name}: _registry.groups['{key}'] 在后端无注册"
                    )
            for key in registry.get("configSections", {}):
                section = key.removeprefix("entity/")
                if section not in sections:
                    problems.append(
                        f"{entity_dir.name}: _registry.configSections['{key}'] 在后端无注册"
                    )
        assert not problems, "\n".join(problems)

    def test_backend_groups_covered(self) -> None:
        """实体静态注册的工具组必须有 _registry.groups 翻译（防新组漏译）。"""
        problems: list[str] = []
        for entity_dir in _entity_dirs():
            groups, _sections = _backend_keys(entity_dir)
            if not groups:
                continue
            locales = _load_locales(entity_dir)
            covered = (
                locales.get("zh", {}).get("_registry", {}).get("groups", {})
                if locales else {}
            )
            for group in sorted(groups - _GROUP_EXEMPTIONS):
                if group not in covered:
                    problems.append(
                        f"{entity_dir.name}: 后端组 '{group}' 缺少 "
                        f"panels/locales 的 _registry.groups 翻译"
                    )
        assert not problems, "\n".join(problems)
