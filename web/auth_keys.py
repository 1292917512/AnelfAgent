"""WebUI / OpenAI 网关鉴权辅助。"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Optional

from core.path import ConfigPaths

_API_KEY_PREFIX = "sk-anelf-"


def _webui_path() -> Path:
    return Path(ConfigPaths.WEBUI_CONFIG)


def load_webui_config() -> dict[str, Any]:
    path = _webui_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text("utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_webui_config(cfg: dict[str, Any]) -> None:
    """原子写 webui.json（tmp 文件 + os.replace，避免中断产生截断文件）。"""
    path = _webui_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=str(path.parent), prefix=".webui.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def hash_api_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


def mask_api_key(raw_or_prefix: str) -> str:
    if not raw_or_prefix:
        return ""
    if len(raw_or_prefix) <= 8:
        return "****"
    return f"{raw_or_prefix[:8]}****{raw_or_prefix[-4:]}"


def list_api_keys(*, include_hash: bool = False) -> list[dict[str, Any]]:
    cfg = load_webui_config()
    auth = cfg.get("auth") or {}
    keys = auth.get("api_keys") or []
    result: list[dict[str, Any]] = []
    for item in keys:
        if not isinstance(item, dict):
            continue
        entry = {
            "id": item.get("id", ""),
            "name": item.get("name", ""),
            "key_prefix": item.get("key_prefix", ""),
            "masked_key": mask_api_key(str(item.get("key_prefix", ""))),
            "created_at": item.get("created_at", 0),
            "last_used_at": item.get("last_used_at"),
        }
        if include_hash:
            entry["key_hash"] = item.get("key_hash", "")
        result.append(entry)
    return result


def create_api_key(*, name: str = "") -> dict[str, Any]:
    raw = f"{_API_KEY_PREFIX}{secrets.token_urlsafe(32)}"
    entry = {
        "id": f"ak_{uuid.uuid4().hex[:12]}",
        "name": name or "default",
        "key_prefix": raw[:12],
        "key_hash": hash_api_key(raw),
        "created_at": int(time.time()),
        "last_used_at": None,
    }
    cfg = load_webui_config()
    auth = cfg.setdefault("auth", {})
    keys = list(auth.get("api_keys") or [])
    keys.append(entry)
    auth["api_keys"] = keys
    save_webui_config(cfg)
    return {
        "id": entry["id"],
        "name": entry["name"],
        "api_key": raw,
        "key_prefix": entry["key_prefix"],
        "masked_key": mask_api_key(entry["key_prefix"]),
        "created_at": entry["created_at"],
    }


def delete_api_key(key_id: str) -> bool:
    cfg = load_webui_config()
    auth = cfg.get("auth") or {}
    keys = list(auth.get("api_keys") or [])
    new_keys = [item for item in keys if not (
        isinstance(item, dict) and item.get("id") == key_id
    )]
    if len(new_keys) == len(keys):
        return False
    auth["api_keys"] = new_keys
    cfg["auth"] = auth
    save_webui_config(cfg)
    return True


def rotate_api_key(key_id: str) -> Optional[dict[str, Any]]:
    cfg = load_webui_config()
    auth = cfg.get("auth") or {}
    keys = list(auth.get("api_keys") or [])
    target: Optional[dict[str, Any]] = None
    for item in keys:
        if isinstance(item, dict) and item.get("id") == key_id:
            target = item
            break
    if target is None:
        return None
    raw = f"{_API_KEY_PREFIX}{secrets.token_urlsafe(32)}"
    target["key_prefix"] = raw[:12]
    target["key_hash"] = hash_api_key(raw)
    target["created_at"] = int(time.time())
    target["last_used_at"] = None
    auth["api_keys"] = keys
    cfg["auth"] = auth
    save_webui_config(cfg)
    return {
        "id": target["id"],
        "name": target.get("name", ""),
        "api_key": raw,
        "key_prefix": target["key_prefix"],
        "masked_key": mask_api_key(target["key_prefix"]),
        "created_at": target["created_at"],
    }


def verify_bearer_api_key(token: str) -> bool:
    """校验 Bearer API Key。未配置任何 key 时拒绝 /v1 访问。"""
    token = (token or "").strip()
    if not token:
        return False
    token_hash = hash_api_key(token)
    cfg = load_webui_config()
    auth = cfg.get("auth") or {}
    keys = auth.get("api_keys") or []
    if not keys:
        return False
    matched = False
    for item in keys:
        if not isinstance(item, dict):
            continue
        if item.get("key_hash") == token_hash:
            item["last_used_at"] = int(time.time())
            matched = True
            break
    if matched:
        cfg["auth"] = auth
        try:
            save_webui_config(cfg)
        except Exception:
            pass
    return matched


def extract_bearer_token(authorization: str) -> str:
    value = (authorization or "").strip()
    if not value.lower().startswith("bearer "):
        return ""
    return value[7:].strip()
