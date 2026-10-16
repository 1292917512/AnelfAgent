"""模块自声明凭据，共用存储、脱敏与热卸载契约。

Model Experience:
    模型通过凭据工具看到归属、字段和配置状态，不获得密钥原文。
    清单按需返回，每条增加约 20 token 的归属信息。
    不自动注入上下文，不改变稳定前缀。
"""

from __future__ import annotations

import json
import os
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from core.file_utils import atomic_write_text
from core.log import log
from core.path import config_dir
from core.registration import belongs_to, caller_module, record_mapping


@dataclass(frozen=True)
class CredentialField:
    """模块声明的可编辑字段；敏感值仅返回掩码。"""

    key: str
    label: str = ""
    secret: bool = True
    multiline: bool = False
    required: bool = True


@dataclass(frozen=True)
class _Registration:
    module: str
    title: str
    owner_title: str
    description: str
    domain: str
    domains: tuple[str, ...]
    fields: tuple[CredentialField, ...]
    destination: str
    editor: str


_registry: dict[str, _Registration] = {}
_lock = threading.RLock()
_cache: dict[str, dict[str, Any]] | None = None
_cache_mtime: int = -1


def _path() -> str:
    return os.path.join(config_dir(), "provider_keys.json")


def register_provider_key(
    name: str, domain: str, title: str, description: str = "", *,
    fields: tuple[CredentialField, ...], domains: tuple[str, ...] = (),
    destination: str = "", editor: str = "", owner_title: str = "",
) -> None:
    """登记所属模块的字段或专属编辑器；目的地为站内路由，编辑器由同模块贡献。"""
    if not re.fullmatch(r"[a-zA-Z0-9_-]+", name):
        raise ValueError("凭据名称无效")
    if bool(fields) == bool(editor):
        raise ValueError("凭据必须声明字段或专属编辑器，不能同时声明")
    if len({field.key for field in fields}) != len(fields) or any(
        not re.fullmatch(r"[a-zA-Z0-9_]+", field.key) for field in fields
    ):
        raise ValueError("凭据字段无效或重复")
    if editor and not re.fullmatch(r"[a-z][a-z0-9-]*", editor):
        raise ValueError("凭据编辑器标识无效")
    if destination and (not destination.startswith("/") or destination.startswith("//") or "\\" in destination):
        raise ValueError("凭据入口必须为站内路由")
    module = caller_module()
    with _lock:
        current = _registry.get(name)
        if current and current.module != module:
            raise ValueError(f"凭据已由其他模块登记: {name}")
        record_mapping(_registry, name, owner=module)
        _registry[name] = _Registration(module, title, owner_title, description, domain, domains or (domain,), fields, destination, editor)


def unregister_provider_keys_by_module(module: str) -> None:
    """卸载声明，保留已配置值以供模块重新加载。"""
    with _lock:
        for name, entry in list(_registry.items()):
            if belongs_to(entry.module, module):
                _registry.pop(name)


def _load() -> dict[str, dict[str, Any]]:
    global _cache, _cache_mtime
    with _lock:
        try:
            mtime = os.stat(_path()).st_mtime_ns
        except FileNotFoundError:
            _cache, _cache_mtime = {}, -1
            return {}
        if _cache is not None and mtime == _cache_mtime:
            return _cache
        with open(_path(), encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict) or any(not isinstance(value, dict) for value in data.values()):
            raise ValueError("凭据文件格式无效，请检查配置文件")
        _cache, _cache_mtime = data, mtime
        return data


def _save(data: dict[str, dict[str, Any]]) -> None:
    global _cache, _cache_mtime
    os.makedirs(os.path.dirname(_path()), exist_ok=True)
    atomic_write_text(Path(_path()), json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    os.chmod(_path(), 0o600)
    _cache, _cache_mtime = data, os.stat(_path()).st_mtime_ns


def get_provider_key(name: str, field: str = "api_key") -> str:
    """读取单个字段，未配置返回空串。"""
    return str(_load().get(name, {}).get(field, "") or "").strip()


def set_provider_fields(name: str, values: dict[str, str]) -> None:
    """原子更新一张卡片的字段；缺省保持原值，显式空串清除。"""
    if not re.fullmatch(r"[a-zA-Z0-9_-]+", name) or not values or any(
        not re.fullmatch(r"[a-zA-Z0-9_]+", key) or not isinstance(value, str) for key, value in values.items()
    ):
        raise ValueError("凭据名称或字段无效")
    with _lock:
        meta = _registry.get(name)
        if meta and (meta.editor or set(values) - {field.key for field in meta.fields}):
            raise ValueError("请使用该凭据登记的字段或专属授权入口")
        data = dict(_load())
        entry = dict(data.get(name, {}))
        for key, value in values.items():
            if value.strip():
                entry[key] = value.strip()
            else:
                entry.pop(key, None)
        if entry:
            data[name] = entry
        else:
            data.pop(name, None)
        _save(data)
    log(f"组件凭据已更新: {name}", "INFO", tag="凭据")


def set_provider_key(name: str, field: str, value: str) -> None:
    """更新单个字段，复用卡片的校验与原子写入。"""
    set_provider_fields(name, {field: value})


def _mask(value: str) -> str:
    return value[:4] + "…" + value[-4:] if len(value) > 12 else ""


def _owner(module: str) -> str:
    parts = module.split(".")
    if len(parts) > 1 and parts[0] in {"entities", "channels"}:
        return f"{'entity' if parts[0] == 'entities' else 'channel'}:{parts[1]}"
    return f"core:{module}"


def list_provider_keys(domain: str = "") -> list[dict[str, Any]]:
    """返回归属与脱敏状态；未登记字段一律视为敏感，不猜测其用途。"""
    with _lock:
        stored = _load()
        entries: list[dict[str, Any]] = []
        for name in sorted(set(_registry) | set(stored)):
            meta = _registry.get(name)
            if domain and (meta is None or domain not in meta.domains):
                continue
            values = stored.get(name, {})
            definitions = meta.fields if meta else tuple(CredentialField(key) for key in values)
            fields = []
            for field in definitions:
                raw = str(values.get(field.key, "") or "").strip()
                fields.append({"key": field.key, "label": field.label or field.key,
                               "value": _mask(raw) if field.secret else raw, "configured": bool(raw),
                               "secret": field.secret, "multiline": field.multiline, "required": field.required})
            required = [field for field in fields if field["required"]]
            configured = None if meta and meta.editor else bool(fields) and all(field["configured"] for field in (required or fields))
            entries.append({"name": name, "title": meta.title if meta else name,
                            "description": meta.description if meta else "", "domain": meta.domain if meta else "",
                            "owner_title": meta.owner_title if meta else "",
                            "domains": list(meta.domains) if meta else [], "owner": _owner(meta.module) if meta else "",
                            "destination": meta.destination if meta else "", "editor": meta.editor if meta else "",
                            "fields": fields, "configured": configured, "unregistered": meta is None})
        return entries


def reset_for_tests() -> None:
    """重置凭据声明和缓存。"""
    global _cache, _cache_mtime
    with _lock:
        _registry.clear()
        _cache, _cache_mtime = None, -1
