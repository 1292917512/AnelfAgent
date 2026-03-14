"""反思系统独立配置。

从 config/introspection.json 加载，管理所有反思单元的开关、提示词和参数。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional

from core.log import log

_CONFIG_PATH = Path("config/introspection.json")


@dataclass
class UnitConfig:
    """单个反思单元的配置。"""

    enabled: bool = True
    scope: str = "any"
    prompt: str = ""
    params: Dict[str, Any] = field(default_factory=dict)


@dataclass
class IntrospectionConfig:
    """反思系统全局配置。"""

    enabled: bool = True
    analysis_temperature: float = 0.7
    min_conversations_for_analysis: int = 3
    reflect_min_hours: float = 1.0
    reflect_max_hours: float = 6.0
    last_reflect_time: float = 0.0
    units: Dict[str, UnitConfig] = field(default_factory=dict)

    def get_unit(self, name: str) -> UnitConfig:
        """获取单元配置；未在 JSON 中配置时默认启用（enabled=True）。"""
        return self.units.get(name, UnitConfig(enabled=True))

    @staticmethod
    def load(path: Optional[Path] = None) -> IntrospectionConfig:
        """从 JSON 加载配置；文件不存在时尝试从 mind_config.json 迁移后创建默认配置。"""
        p = path or _CONFIG_PATH
        if p.exists():
            return _parse_config(p)
        return _migrate_and_create(p)

    def save(self, path: Optional[Path] = None) -> None:
        """持久化配置到 JSON 文件。"""
        p = path or _CONFIG_PATH
        p.parent.mkdir(parents=True, exist_ok=True)
        data: Dict[str, Any] = {
            "enabled": self.enabled,
            "analysis_temperature": self.analysis_temperature,
            "min_conversations_for_analysis": self.min_conversations_for_analysis,
            "reflect_min_hours": self.reflect_min_hours,
            "reflect_max_hours": self.reflect_max_hours,
            "last_reflect_time": self.last_reflect_time,
            "units": {},
        }
        for name, uc in self.units.items():
            unit_data: Dict[str, Any] = {
                "enabled": uc.enabled,
                "scope": uc.scope,
            }
            if uc.prompt:
                unit_data["prompt"] = uc.prompt
            if uc.params:
                unit_data.update(uc.params)
            data["units"][name] = unit_data
        p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _parse_config(p: Path) -> IntrospectionConfig:
    """解析 JSON 配置文件为 IntrospectionConfig。"""
    try:
        raw = json.loads(p.read_text("utf-8"))
    except Exception as exc:
        log(f"反思配置加载失败，使用默认值: {exc}", "WARNING")
        return _default_config()

    units: Dict[str, UnitConfig] = {}
    for name, unit_raw in raw.get("units", {}).items():
        known_keys = {"enabled", "scope", "prompt"}
        params = {k: v for k, v in unit_raw.items() if k not in known_keys}
        units[name] = UnitConfig(
            enabled=unit_raw.get("enabled", True),
            scope=unit_raw.get("scope", "any"),
            prompt=unit_raw.get("prompt", ""),
            params=params,
        )

    return IntrospectionConfig(
        enabled=raw.get("enabled", True),
        analysis_temperature=float(raw.get("analysis_temperature", 0.7)),
        min_conversations_for_analysis=int(raw.get("min_conversations_for_analysis", 3)),
        reflect_min_hours=float(raw.get("reflect_min_hours", 1.0)),
        reflect_max_hours=float(raw.get("reflect_max_hours", 6.0)),
        last_reflect_time=float(raw.get("last_reflect_time", 0.0)),
        units=units,
    )


def _migrate_and_create(p: Path) -> IntrospectionConfig:
    """首次运行时从 mind_config.json 迁移标量参数，生成默认配置。

    prompt 不再内嵌到 JSON（由各反思单元类的 default_prompt 提供默认值），
    用户若需自定义 prompt 可手动编辑 introspection.json 的 units[*].prompt 字段。
    """
    last_reflect = 0.0
    analysis_temp = 0.7
    min_conv = 3
    reflect_hours = 1.0

    mind_cfg_path = Path("config/mind_config.json")
    if mind_cfg_path.exists():
        try:
            mind_data = json.loads(mind_cfg_path.read_text("utf-8"))
            if "last_reflect_time" in mind_data:
                last_reflect = float(mind_data["last_reflect_time"])
            if "analysis_temperature" in mind_data:
                analysis_temp = float(mind_data["analysis_temperature"])
            if "min_conversations_for_analysis" in mind_data:
                min_conv = int(mind_data["min_conversations_for_analysis"])
            if "reflect_min_hours" in mind_data:
                reflect_hours = float(mind_data["reflect_min_hours"])
            log("从 mind_config.json 迁移反思参数")
        except Exception as exc:
            log(f"迁移 mind_config 失败: {exc}", "WARNING")

    cfg = _default_config(
        analysis_temperature=analysis_temp,
        min_conversations_for_analysis=min_conv,
        reflect_min_hours=reflect_hours,
        last_reflect_time=last_reflect,
    )
    # reflect_max_hours 无旧配置来源，使用默认值 6.0
    cfg.save(p)
    return cfg


def _default_config(
    analysis_temperature: float = 0.7,
    min_conversations_for_analysis: int = 3,
    reflect_min_hours: float = 1.0,
    reflect_max_hours: float = 6.0,
    last_reflect_time: float = 0.0,
) -> IntrospectionConfig:
    """构建默认配置；prompt 由各单元类的 default_prompt 提供，不写入 JSON。"""
    return IntrospectionConfig(
        analysis_temperature=analysis_temperature,
        min_conversations_for_analysis=min_conversations_for_analysis,
        reflect_min_hours=reflect_min_hours,
        reflect_max_hours=reflect_max_hours,
        last_reflect_time=last_reflect_time,
        units={
            "memory_health": UnitConfig(
                scope="global",
                params={
                    "memory_warn_threshold": 200,
                    "entity_merge_threshold": 5,
                    "reflection_merge_threshold": 10,
                },
            ),
        },
    )


# 单例
_instance: Optional[IntrospectionConfig] = None


def get_introspection_config() -> IntrospectionConfig:
    global _instance
    if _instance is None:
        _instance = IntrospectionConfig.load()
    return _instance


def reload_introspection_config() -> IntrospectionConfig:
    global _instance
    _instance = IntrospectionConfig.load()
    return _instance
