"""判断服务商与模型目录；Web、AI 和运行时共享校验与原子存储。

模型看到：选定模型接收原有 System One 请求，配置操作仅返回脱敏目录。
Token：仅故障时按显式配置的优先级切换服务，不重复评判已有答案。
缓存：目录按文件签名缓存，不参与主模型上下文与前缀构建。
"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from agent.judgment.client import SystemOneClient
from agent.judgment.types import JudgmentError
from core.config import expand_env_refs, is_masked_secret, mask_secret
from core.file_utils import atomic_write_text, file_write_lock
from core.path import ConfigPaths

CatalogAction = Literal["save_provider", "delete_provider", "save_model", "delete_model", "set_default", "set_priority"]


def _validate_id(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}", value):
        raise ValueError("ID 须为 1–80 位字母、数字、点、横线或下划线，以字母或数字开头")
    return value


class DecisionModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(default="", max_length=160)
    model: str = Field(min_length=1, max_length=256)


class DecisionProvider(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=160)
    base_url: str = Field(min_length=1, max_length=2048)
    use_proxy: bool = Field(default=False, strict=True, description="使用全局代理地址；关闭时始终直连")
    api_key: str = Field(default="", repr=False, max_length=8192)
    models: dict[str, DecisionModel] = Field(default_factory=dict, max_length=64)

    @field_validator("base_url")
    @classmethod
    def valid_url(cls, value: str) -> str:
        try:
            SystemOneClient.resolve_endpoint(value)
        except JudgmentError as exc:
            raise ValueError(str(exc)) from exc
        return value.rstrip("/")


class JudgmentCatalog(BaseModel):
    model_config = ConfigDict(extra="forbid")
    providers: dict[str, DecisionProvider] = Field(default_factory=dict, max_length=32)
    default_model: str = ""
    fallback_models: list[str] = Field(default_factory=list, max_length=64)

    @model_validator(mode="after")
    def validate_references(self) -> JudgmentCatalog:
        models: set[str] = set()
        for pid, provider in self.providers.items():
            _validate_id(pid)
            for mid in provider.models:
                _validate_id(mid)
                if mid in models:
                    raise ValueError(f"模型 ID 重复: {mid}")
                models.add(mid)
        if self.default_model and self.default_model not in models:
            raise ValueError("默认判断模型不存在")
        if len(set(self.fallback_models)) != len(self.fallback_models):
            raise ValueError("判断回退模型不能重复")
        if any(mid not in models or mid == self.default_model for mid in self.fallback_models):
            raise ValueError("判断回退模型须存在，且不能包含默认模型")
        if self.fallback_models and not self.default_model:
            raise ValueError("设置回退模型前须选择默认模型")
        return self

    def prune_selection(self) -> None:
        """删除模型后保留仍存在的候选顺序，首选缺失时提升下一候选。"""
        models = {mid for provider in self.providers.values() for mid in provider.models}
        order = [mid for mid in [self.default_model, *self.fallback_models] if mid in models]
        self.default_model = order[0] if order else ""
        self.fallback_models = order[1:]


def _parse(raw: Any) -> JudgmentCatalog:
    try:
        return JudgmentCatalog.model_validate(raw)
    except ValidationError as exc:
        details = "; ".join(f"{'.'.join(map(str, item['loc']))}: {item['msg']}"
                            for item in exc.errors(include_input=False, include_context=False))
        raise ValueError(f"判断模型配置无效：{details}") from None


@lru_cache(maxsize=8)
def _read(path: str, mtime_ns: int, size: int) -> JudgmentCatalog:
    return _parse(json.loads(Path(path).read_text(encoding="utf-8")))


def load_catalog() -> JudgmentCatalog:
    """读取独立目录快照；配置损坏时明确报错，不用空配置覆盖。"""
    path = Path(ConfigPaths.JUDGMENT)
    with file_write_lock(path):
        if not path.exists():
            return JudgmentCatalog()
        stat = path.stat()
        return _read(str(path.resolve()), stat.st_mtime_ns, stat.st_size).model_copy(deep=True)


def public_catalog(catalog: JudgmentCatalog | None = None) -> dict[str, Any]:
    """返回管理面快照，所有服务商密钥统一脱敏。"""
    result = (catalog or load_catalog()).model_dump()
    for provider in result["providers"].values():
        provider["api_key"] = mask_secret(str(expand_env_refs(provider["api_key"])))
    path = Path(ConfigPaths.JUDGMENT)
    result["revision"] = str(path.stat().st_mtime_ns) if path.exists() else "0"
    return result


def select_model(model_id: str = "", *, catalog: JudgmentCatalog | None = None) -> tuple[str, str, DecisionProvider, DecisionModel] | None:
    """解析显式或默认模型，密钥仅在请求前展开环境变量。"""
    catalog = catalog if catalog is not None else load_catalog()
    selected = model_id or catalog.default_model
    if not selected:
        return None
    for pid, provider in catalog.providers.items():
        if selected in provider.models:
            resolved = provider.model_copy(update={"api_key": str(expand_env_refs(provider.api_key)).strip()})
            return selected, pid, resolved, provider.models[selected]
    raise ValueError(f"判断模型不存在: {selected}")


def update_catalog(action: CatalogAction, identifier: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
    """串行读取、校验和提交目录；空 Key 清除认证，掩码或省略 Key 保留原值。"""
    path = Path(ConfigPaths.JUDGMENT)
    with file_write_lock(path):
        catalog = load_catalog()
        before = {mid: (p.base_url, p.api_key, p.use_proxy, model.model)
                  for p in catalog.providers.values() for mid, model in p.models.items()}
        if action == "set_default":
            catalog.default_model = identifier
            catalog.fallback_models = [mid for mid in catalog.fallback_models if mid != identifier] if identifier else []
        elif action == "set_priority":
            order = (data or {}).get("model_ids")
            if not isinstance(order, list) or not all(isinstance(mid, str) and mid for mid in order):
                raise ValueError("model_ids 须为按优先级排列的模型 ID 数组，空数组关闭原生模型选择")
            if len(set(order)) != len(order):
                raise ValueError("判断模型优先级不能重复")
            catalog.default_model = order[0] if order else ""
            catalog.fallback_models = order[1:]
        elif action == "delete_provider":
            if identifier not in catalog.providers:
                raise ValueError("判断服务商不存在")
            catalog.providers.pop(identifier)
            catalog.prune_selection()
        elif action == "save_provider":
            _validate_id(identifier)
            if data is None:
                raise ValueError("缺少服务商配置")
            old = catalog.providers.get(identifier)
            values = {**(old.model_dump() if old else {}), **data}
            key = values.get("api_key")
            if key is None or (isinstance(key, str) and is_masked_secret(key)):
                values["api_key"] = old.api_key if old else ""
            raw = catalog.model_dump()
            raw["providers"][identifier] = values
            raw["default_model"], raw["fallback_models"] = "", []
            updated = _parse(raw)
            updated.default_model, updated.fallback_models = catalog.default_model, catalog.fallback_models
            updated.prune_selection()
            catalog = updated
        elif action in {"save_model", "delete_model"}:
            _validate_id(identifier)
            owner = next((p for p in catalog.providers.values() if identifier in p.models), None)
            if action == "delete_model":
                if owner is None:
                    raise ValueError("判断模型不存在")
                owner.models.pop(identifier)
                catalog.prune_selection()
            else:
                values = dict(data or {})
                pid = values.pop("provider_id", "")
                if not isinstance(pid, str) or pid not in catalog.providers:
                    raise ValueError("判断服务商不存在")
                raw = catalog.model_dump()
                for entry in raw["providers"].values():
                    entry["models"].pop(identifier, None)
                raw["providers"][pid]["models"][identifier] = values
                catalog = _parse(raw)
        else:
            raise ValueError(f"未知操作: {action}")
        catalog = _parse(catalog.model_dump())
        after = {mid: (p.base_url, p.api_key, p.use_proxy, model.model)
                 for p in catalog.providers.values() for mid, model in p.models.items()}
        atomic_write_text(path, catalog.model_dump_json(indent=2) + "\n")
        _read.cache_clear()
        from agent.judgment.health import judgment_health
        judgment_health.invalidate({mid for mid in before.keys() | after.keys() if before.get(mid) != after.get(mid)})
        return public_catalog(catalog)
