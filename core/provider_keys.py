"""组件凭据中心 — 外部平台 API Key 的统一存取面。

设计约定（模块分离与配置便利的平衡点）：
- 域（声音/视觉/检索…）只提炼能力接口，具体平台由组件实现；组件所需的
  外部凭据统一登记到本中心，落盘 ``config/provider_keys.json``
  （gitignored，只跟踪 example 模板）；
- 三面配置等价：Web（域页签的组件凭据面板）、配置文件（直接编辑
  JSON）、AI（list/set_provider_key 工具）——同一存储，无第二真源；
- 大模型类生成（能走 LLM 接口的图像/视频等）继续走 llm_clients 模型
  配置，不经本中心；实时/特殊协议的语音等模块凭据只走本中心，不依赖
  大模型配置；
- 未登记/未配置凭据的组件自动不可用，其余链路不受影响。

凭据条目结构：{"<provider>": {"api_key": "...", ...附加字段}}；附加字段
用于同平台的配套参数（如 coding plan 的 host），敏感字段列出时脱敏。
"""

from __future__ import annotations

import json
import os
import threading
from typing import Any, Dict, List, Optional

from core.log import log
from core.path import config_dir

_LOG_TAG = "凭据"

_SECRET_FIELDS = frozenset({"api_key"})

_registry: Dict[str, List[Dict[str, Any]]] = {}
_lock = threading.Lock()
_cache: Optional[Dict[str, Dict[str, Any]]] = None
_cache_mtime: float = -1.0


def _path() -> str:
    return os.path.join(config_dir(), "provider_keys.json")


def register_provider_key(
    name: str,
    domain: str,
    title: str,
    description: str = "",
    extra_fields: Optional[List[Dict[str, Any]]] = None,
) -> None:
    """登记一个组件凭据条目（组件导入时调用，幂等）。

    Args:
        name: 提供者标识（如 minimax / dashscope / minimax_coding_plan）
        domain: 所属域（sound / vision / retrieval…，域页签按此过滤）
        title: 展示名
        description: 用途说明（面板与 AI 工具展示）
        extra_fields: api_key 之外的附加字段声明
            [{"key": "api_host", "label": "接入点", "default": "...", "secret": False}]
    """
    with _lock:
        # 同一凭据可挂多个域面板（如订阅 Key 声音/检索两域共用）：幂等去重追加
        entries = _registry.setdefault(name, [])
        entry = {
            "domain": domain,
            "title": title,
            "description": description,
            "extra_fields": list(extra_fields or []),
        }
        if entry not in entries:
            entries.append(entry)


def _load() -> Dict[str, Dict[str, Any]]:
    global _cache, _cache_mtime
    with _lock:
        try:
            mtime = os.stat(_path()).st_mtime
        except OSError:
            _cache, _cache_mtime = {}, -1.0
            return {}
        if _cache is not None and mtime == _cache_mtime:
            return _cache
        try:
            with open(_path(), encoding="utf-8") as fh:
                data = json.load(fh)
            _cache = data if isinstance(data, dict) else {}
        except Exception as exc:
            log(f"凭据文件读取失败（视为空）: {exc}", "WARNING", tag=_LOG_TAG)
            _cache = {}
        _cache_mtime = mtime
        return _cache


def _save(data: Dict[str, Dict[str, Any]]) -> None:
    global _cache, _cache_mtime
    with _lock:
        os.makedirs(os.path.dirname(_path()), exist_ok=True)
        tmp = _path() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
        os.replace(tmp, _path())
        _cache, _cache_mtime = dict(data), os.stat(_path()).st_mtime


def get_provider_key(name: str, field: str = "api_key") -> str:
    """读一个凭据字段（未配置返回空串）。"""
    entry = _load().get(name)
    if not isinstance(entry, dict):
        return ""
    return str(entry.get(field, "") or "").strip()


def set_provider_key(name: str, field: str, value: str) -> None:
    """写一个凭据字段（空值删除该字段；条目字段清空后整体摘除）。"""
    data = dict(_load())
    entry = dict(data.get(name) or {})
    value = str(value or "").strip()
    if value:
        entry[field] = value
    else:
        entry.pop(field, None)
    if entry:
        data[name] = entry
    else:
        data.pop(name, None)
    _save(data)
    log(f"组件凭据已更新: {name}.{field}", "INFO", tag=_LOG_TAG)


def _mask(value: str) -> str:
    if not value:
        return ""
    return value[:4] + "…" + value[-4:] if len(value) > 12 else "已配置"


def list_provider_keys(domain: str = "") -> List[Dict[str, Any]]:
    """凭据条目清单（值脱敏；domain 非空时按域过滤）。

    含文件里存在但未登记的条目（config 手填场景），标记 unregistered。
    """
    entries: List[Dict[str, Any]] = []
    for name, metas in _registry.items():
        stored = _load().get(name) or {}
        for meta in metas:
            if domain and meta["domain"] != domain:
                continue
            fields: List[Dict[str, Any]] = [{
                "key": "api_key",
                "value": _mask(str(stored.get("api_key", "") or "")),
                "configured": bool(str(stored.get("api_key", "") or "").strip()),
                "secret": True,
            }]
            for extra in meta["extra_fields"]:
                raw = str(stored.get(extra["key"], "") or "").strip()
                secret = bool(extra.get("secret", False))
                fields.append({
                    "key": extra["key"],
                    "label": extra.get("label", extra["key"]),
                    "value": _mask(raw) if secret else raw,
                    "configured": bool(raw),
                    "secret": secret,
                })
            entries.append({
                "name": name, "domain": meta["domain"], "title": meta["title"],
                "description": meta["description"], "fields": fields,
            })
    for name in sorted(set(_load()) - set(_registry)):
        stored = _load().get(name) or {}
        entries.append({
            "name": name, "domain": "", "title": name,
            "description": "（文件中存在但未被任何组件登记）",
            "fields": [{
                "key": k, "value": _mask(str(v)) if k in _SECRET_FIELDS else str(v),
                "configured": bool(str(v or "").strip()), "secret": k in _SECRET_FIELDS,
            } for k, v in stored.items()],
            "unregistered": True,
        })
    return entries


def reset_for_tests() -> None:
    """清空注册表与缓存（测试隔离用）。"""
    global _cache, _cache_mtime
    with _lock:
        _registry.clear()
        _cache, _cache_mtime = None, -1.0
