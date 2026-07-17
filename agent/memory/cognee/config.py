"""Cognee 可选记忆后端配置。"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from core.log import log
from core.path import ConfigPaths, project_root


@dataclass(slots=True)
class CogneeConfig:
    """Cognee 投影与联邦召回配置。"""

    enabled: bool = False
    sync_enabled: bool = True
    recall_enabled: bool = True
    data_root: str = ConfigPaths.COGNEE_DATA_DIR
    dataset_prefix: str = "anelf"
    timeout_seconds: float = 30.0
    sync_interval_seconds: float = 5.0
    sync_batch_size: int = 20
    max_retries: int = 5
    native_weight: float = 1.0
    cognee_weight: float = 0.8
    rrf_k: int = 60
    recall_pool_multiplier: int = 3
    search_types: list[str] = field(
        default_factory=lambda: ["CHUNKS", "CHUNKS_LEXICAL"],
    )

    @property
    def absolute_data_root(self) -> str:
        path = Path(self.data_root)
        if not path.is_absolute():
            path = Path(project_root()) / path
        return str(path.resolve())

    def normalized(self) -> "CogneeConfig":
        self.timeout_seconds = max(1.0, float(self.timeout_seconds))
        self.sync_interval_seconds = max(0.5, float(self.sync_interval_seconds))
        self.sync_batch_size = max(1, int(self.sync_batch_size))
        self.max_retries = max(1, int(self.max_retries))
        self.native_weight = max(0.0, float(self.native_weight))
        self.cognee_weight = max(0.0, float(self.cognee_weight))
        self.rrf_k = max(1, int(self.rrf_k))
        self.recall_pool_multiplier = max(1, int(self.recall_pool_multiplier))
        self.dataset_prefix = self.dataset_prefix.strip() or "anelf"
        self.search_types = [str(item).strip().upper() for item in self.search_types if str(item).strip()]
        return self

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def load_cognee_config() -> CogneeConfig:
    """读取 Cognee 配置；缺失或损坏时返回安全默认值。"""
    path = Path(ConfigPaths.COGNEE_CONFIG)
    if not path.is_absolute():
        path = Path(project_root()) / path
    if not path.exists():
        return CogneeConfig()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        allowed = CogneeConfig.__dataclass_fields__.keys()
        values = {key: value for key, value in raw.items() if key in allowed}
        return CogneeConfig(**values).normalized()
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        log(f"Cognee 配置加载失败，使用默认值: {exc}", "WARNING")
        return CogneeConfig()


def save_cognee_config(config: CogneeConfig) -> None:
    """持久化 Cognee 配置。"""
    path = Path(ConfigPaths.COGNEE_CONFIG)
    if not path.is_absolute():
        path = Path(project_root()) / path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(config.normalized().to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
