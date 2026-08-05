"""媒体库配置管理：读写 entities/media/config.json（进程级缓存，写后失效）。"""

from __future__ import annotations

import copy
import json
import os
import threading
from typing import Any, Dict, List

from core.log import log

_CONFIG_FILE = os.path.join(os.path.dirname(__file__), "config.json")
_lock = threading.Lock()
_cache: Dict[str, Any] | None = None

# 各能力的默认 provider 优先级链：models=模型配置（llm_clients.json）中的对应类型模型，
# minimax=entities/minimax 直连模块。仅声明了能力的 provider 才会被实际尝试。
_DEFAULT_PROVIDER_PRIORITY: Dict[str, List[str]] = {
    "vision": ["models", "minimax"],
    "tts": ["models", "minimax"],
    "voice_mgmt": ["models", "minimax"],
    "image_gen": ["models", "minimax"],
    "image_edit": ["models"],
    "asr": ["models"],
    "music": ["models"],
    "video": ["models"],
    "rerank": ["models"],
}

_DEFAULTS: Dict[str, Any] = {
    "provider_priority": _DEFAULT_PROVIDER_PRIORITY,
    "default_voice": "",
    "default_reference_audio": "",
    "default_reference_text": "",
    "defaults": {
        "image_size": "1024x1024",
        "video_resolution": "",
        "video_duration": 0,
    },
    "style_presets": {},
}


def _merge_defaults(data: Dict[str, Any]) -> Dict[str, Any]:
    """缺失键用默认值补齐（嵌套 dict 逐层合并），不覆盖已有值。"""
    merged = copy.deepcopy(_DEFAULTS)
    for key, value in data.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key].update(value)
        else:
            merged[key] = value
    return merged


def load_config() -> Dict[str, Any]:
    """加载媒体库配置（进程级缓存，缺失字段自动补默认值）。"""
    global _cache
    with _lock:
        if _cache is not None:
            return _cache
        try:
            with open(_CONFIG_FILE, encoding="utf-8") as f:
                _cache = _merge_defaults(json.load(f))
        except FileNotFoundError:
            _cache = copy.deepcopy(_DEFAULTS)
        except Exception as e:
            log(f"媒体库配置加载失败，使用默认值: {e}", "ERROR", tag="媒体")
            _cache = copy.deepcopy(_DEFAULTS)
        return _cache


def reload_config() -> Dict[str, Any]:
    """强制重新加载配置（热更新场景）。"""
    global _cache
    with _lock:
        _cache = None
    return load_config()


def save_config(data: Dict[str, Any]) -> Dict[str, Any]:
    """写回 config.json 并刷新缓存，返回生效配置。"""
    global _cache
    merged = _merge_defaults(data)
    with open(_CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, indent=2)
        f.write("\n")
    with _lock:
        _cache = merged
    return merged


def provider_chain(capability: str) -> List[str]:
    """指定能力的 provider 优先级链（配置缺失时回退默认链）。"""
    chain = load_config().get("provider_priority", {}).get(capability)
    if isinstance(chain, list) and chain:
        return [str(p) for p in chain]
    return list(_DEFAULT_PROVIDER_PRIORITY.get(capability, ["models"]))


def get_default(key: str, fallback: Any = "") -> Any:
    """读取 defaults 段的默认参数（image_size / video_resolution 等）。"""
    return load_config().get("defaults", {}).get(key, fallback)


def apply_style(prompt: str, style: str) -> str:
    """将风格预设拼接到提示词末尾；未命中预设时按原始风格描述拼接。"""
    if not style.strip():
        return prompt
    presets = load_config().get("style_presets", {}) or {}
    suffix = presets.get(style.strip(), style.strip())
    return f"{prompt}, {suffix}"


_SCALAR_KEYS = ("default_voice", "default_reference_audio", "default_reference_text")
_DEFAULT_PARAMS_KEYS = ("image_size", "video_resolution", "video_duration")


def update_key(key: str, value: Any) -> Dict[str, Any]:
    """按点路径更新单个配置项并持久化，返回生效配置。

    支持的键：default_voice / default_reference_audio / default_reference_text、
    defaults.<image_size|video_resolution|video_duration>、
    style_presets.<预设名>（value 为空串=删除该预设）、provider_priority.<能力名>。
    provider_priority 的合法性（能力名/provider 名）由调用方校验。
    """
    cfg = load_config()
    if key in _SCALAR_KEYS:
        cfg[key] = str(value)
    elif key.startswith("defaults."):
        sub = key.split(".", 1)[1]
        if sub not in _DEFAULT_PARAMS_KEYS:
            raise ValueError(f"不支持的默认参数键: {key}（可选: {' / '.join(_DEFAULT_PARAMS_KEYS)}）")
        defaults = dict(cfg.get("defaults", {}))
        defaults[sub] = int(value) if sub == "video_duration" else str(value)
        cfg["defaults"] = defaults
    elif key.startswith("style_presets."):
        name = key.split(".", 1)[1].strip()
        if not name:
            raise ValueError("style_presets 键必须带预设名，如 style_presets.nekomimi_maid")
        presets = dict(cfg.get("style_presets", {}))
        if str(value).strip():
            presets[name] = str(value)
        else:
            presets.pop(name, None)
        cfg["style_presets"] = presets
    elif key.startswith("provider_priority."):
        cap = key.split(".", 1)[1].strip()
        if not isinstance(value, list) or not all(isinstance(p, str) for p in value):
            raise ValueError("provider_priority 的值必须是字符串数组")
        priority = dict(cfg.get("provider_priority", {}))
        priority[cap] = list(value)
        cfg["provider_priority"] = priority
    else:
        raise ValueError(f"不支持的配置键: {key}")
    return save_config(cfg)
