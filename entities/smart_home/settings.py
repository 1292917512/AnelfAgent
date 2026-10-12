"""智能家居配置存储：统一配置入口、原子持久化与旧实体配置兼容读取。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from core.config import ConfigManager, ConfigStore
from core.file_utils import atomic_write_text
from core.path import ConfigPaths


class SmartHomeConfigStore(ConfigStore):
    """配置位于 ConfigPaths 配置根；首次保存前兼容旧 config.json，不删除旧文件。"""

    def __init__(self, legacy_path: Path) -> None:
        self._legacy_path = legacy_path
        self._values: dict[str, Any] = {}

    @property
    def path(self) -> Path:
        return Path(ConfigPaths.APP_CONFIG).parent / "smart_home.json"

    def load(self) -> None:
        source = self.path if self.path.exists() else self._legacy_path
        values = json.loads(source.read_text("utf-8")) if source.exists() else {}
        if not isinstance(values, dict):
            raise ValueError("智能家居配置必须是 JSON 对象")
        self._values = {key: value for key, value in values.items() if key.startswith("smart_home_")}

    def get(self, key: str, default: Any = None) -> Any:
        return ConfigManager.resolve_value(key, self._values.get(key, default))

    def set(self, key: str, value: Any) -> None:
        self._values[key] = value

    def has(self, key: str) -> bool:
        return key in self._values or ConfigManager.environment_override(key) is not None

    def save(self) -> None:
        atomic_write_text(self.path, json.dumps(self._values, ensure_ascii=False, indent=2))
