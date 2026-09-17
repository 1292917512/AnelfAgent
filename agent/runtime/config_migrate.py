"""旧实体配置一次性迁移 — 媒体库/网络工具实体的本地 config.json 导入统一配置体系。

实体核心化后，原 entities/media/config.json 与 entities/web/config.json
（gitignored 本地文件，仓库删除目录后可能仍残留在老部署上）在启动时导入
ConfigManager 对应键，导入完成后将源文件重命名为 config.json.migrated
（幂等：标记文件存在即跳过；目标键已有非空值时不覆盖）。
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict

from core.config import ConfigManager
from core.log import log

_LOG_TAG = "启动"

# 媒体能力名 → 视觉能力名（asr/rerank 为内部模型链，无需迁移优先级）
_MEDIA_CAP_TO_VISUAL = {
    "vision": "understand",
    "image_gen": "image_gen",
    "image_edit": "image_edit",
    "video": "video",
}
_MEDIA_SOUND_CAPS = ("tts", "voice_mgmt", "music")


def _load_json(path: str) -> Dict[str, Any]:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _set_if_empty(key: str, value: Any) -> bool:
    """目标键为空/默认值时写入（不覆盖用户已配置的新值），返回是否写入。"""
    if value in (None, "", {}, []):
        return False
    current = ConfigManager.get(key, None)
    if current not in (None, "", {}, []):
        return False
    ConfigManager.set(key, value)
    return True


def _archive(path: str) -> None:
    try:
        os.replace(path, path + ".migrated")
    except OSError as e:
        log(f"旧配置文件归档失败 {path}: {e}", "WARNING", tag=_LOG_TAG)


def _migrate_media_config(path: str) -> int:
    """媒体库配置 → vision_*/sound_* 键（默认音色字段随旧体系废止，不再搬运）。"""
    data = _load_json(path)
    if not data:
        return 0
    count = 0

    defaults = data.get("defaults")
    if isinstance(defaults, dict):
        count += _set_if_empty("vision_default_image_size", str(defaults.get("image_size", "") or ""))
        count += _set_if_empty("vision_default_video_resolution", str(defaults.get("video_resolution", "") or ""))
        duration = defaults.get("video_duration", 0)
        if duration:
            count += _set_if_empty("vision_default_video_duration", int(duration))

    presets = data.get("style_presets")
    if isinstance(presets, dict):
        count += _set_if_empty("vision_style_presets", presets)

    priority = data.get("provider_priority")
    if isinstance(priority, dict):
        visual = {
            new_cap: chain for old_cap, new_cap in _MEDIA_CAP_TO_VISUAL.items()
            if isinstance((chain := priority.get(old_cap)), list) and chain
        }
        count += _set_if_empty("vision_provider_priority", visual)
        sound = {
            cap: chain for cap in _MEDIA_SOUND_CAPS
            if isinstance((chain := priority.get(cap)), list) and chain
        }
        count += _set_if_empty("sound_provider_priority", sound)
    return count


def _migrate_web_config(path: str) -> int:
    """网络工具实体配置 → retrieval_* 键。"""
    data = _load_json(path)
    if not data:
        return 0
    count = 0
    count += _set_if_empty("retrieval_proxy", str(data.get("proxy", "") or ""))
    active = data.get("active")
    if isinstance(active, dict):
        cleaned = {k: v for k, v in active.items() if v and v != "auto"}
        count += _set_if_empty("retrieval_active", cleaned)
    disabled = data.get("disabled")
    if isinstance(disabled, list):
        count += _set_if_empty("retrieval_disabled_providers", [str(p) for p in disabled])
    keys = data.get("provider_keys")
    if isinstance(keys, dict):
        count += _set_if_empty("retrieval_bigmodel_api_key", str(keys.get("bigmodel", "") or ""))
    return count



def _minimax_config_path() -> str:
    return os.path.join("entities", "minimax", "config.json")


def _llm_clients_path() -> str:
    from core.path import config_dir

    return os.path.join(config_dir(), "llm_clients.json")


def _migrate_provider_keys() -> None:
    """组件凭据一次性归拢到 config/provider_keys.json（只搬缺失项，不动源文件）。"""
    from core import provider_keys as pk

    def _put(provider: str, field: str, value: str) -> bool:
        value = str(value or "").strip()
        if not value or pk.get_provider_key(provider, field):
            return False
        pk.set_provider_key(provider, field, value)
        return True

    moved = False
    # MiniMax：实体 config.json 的 api_key / coding_plan_*
    mm_config = _minimax_config_path()
    try:
        with open(mm_config, encoding="utf-8") as fh:
            data = json.load(fh)
        moved |= _put("minimax", "api_key", data.get("api_key", ""))
        moved |= _put("minimax_coding_plan", "api_key", data.get("coding_plan_api_key", ""))
        moved |= _put("minimax_coding_plan", "api_host", data.get("coding_plan_api_host", ""))
        for legacy in ("api_key", "coding_plan_api_key", "coding_plan_api_host"):
            data.pop(legacy, None)
        with open(mm_config, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=4)
    except FileNotFoundError:
        pass
    except Exception as e:
        log(f"MiniMax 实体凭据迁移失败（跳过）: {e}", "WARNING", tag=_LOG_TAG)
    # DashScope：llm_clients dashscope 兼容 Key 一次性提取 + 旧配置键
    try:
        with open(_llm_clients_path(), encoding="utf-8") as fh:
            clients = json.load(fh)
        for provider in clients.get("providers", []):
            base = str(provider.get("base_url", "") or "")
            if "dashscope.aliyuncs.com" in base:
                moved |= _put("dashscope", "api_key", provider.get("api_key", ""))
                break
    except Exception:
        pass
    moved |= _put("dashscope", "api_key", ConfigManager.get("dashscope_api_key", ""))
    if moved:
        log("组件凭据已归拢到 config/provider_keys.json", tag=_LOG_TAG)


def migrate_legacy_entity_configs(entities_dir: str = "") -> None:
    """导入旧实体本地配置到统一配置体系（幂等，启动时调用一次）。"""
    if not entities_dir:
        from entities.hotplug import _entities_dir
        entities_dir = str(_entities_dir())

    migrated_any = False
    for rel, handler in (
        (("media", "config.json"), _migrate_media_config),
        (("web", "config.json"), _migrate_web_config),
    ):
        path = os.path.join(entities_dir, *rel)
        if not os.path.exists(path):
            continue
        try:
            count = handler(path)
        except Exception as e:
            log(f"旧实体配置迁移失败 {path}（跳过）: {e}", "WARNING", tag=_LOG_TAG)
            continue
        _archive(path)
        migrated_any = True
        log(f"旧实体配置已导入统一配置体系: {path}（{count} 项）", tag=_LOG_TAG)

    # 旧 SSRF 开关键名迁移（web_ssrf_protection → retrieval_ssrf_protection）
    if ConfigManager.has("web_ssrf_protection") and not ConfigManager.has("retrieval_ssrf_protection"):
        ConfigManager.set("retrieval_ssrf_protection",
                          bool(ConfigManager.get("web_ssrf_protection", True)))
        migrated_any = True

    # 组件凭据归拢：实体 config.json / llm_clients / 旧配置键 → 凭据中心
    _migrate_provider_keys()

    # FunASR 配置归属迁移（音源同步实体键 → 声音系统键）
    for old_key, new_key in (
            ("audiosync_funasr_endpoint", "funasr_endpoint"),
            ("audiosync_funasr_timeout", "funasr_timeout")):
        old_value = str(ConfigManager.get(old_key, "") or "").strip()
        if old_value and not str(ConfigManager.get(new_key, "") or "").strip():
            ConfigManager.set(new_key, old_value)
            migrated_any = True

    if migrated_any:
        ConfigManager.save()
