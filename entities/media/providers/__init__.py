"""媒体能力路由：按配置的 provider 优先级链分发能力调用。

链上每个 provider 依次尝试：
- 未配置（ProviderUnavailable）/不支持（CapabilityNotSupported 或 NotImplementedError）
  → 记录原因并跳过，尝试下一 provider
- 真实调用失败 → 记录错误明细，尝试下一 provider
- 全部失败 → 聚合归因（classify_media_errors）返回结构化错误，附各 provider 失败明细

返回 dict（内部流）：成功含 success=True/provider/产物字段；失败含 error/cause/hint 等
（与 core.tool_errors.tool_error 同构），由 tools 层统一序列化。
"""

from __future__ import annotations

from typing import Any, Dict, List

from core.log import log
from entities._sdk import ErrorCause

from ..config import provider_chain
from .base import (
    CapabilityNotSupported,
    MediaProvider,
    ModelChainError,
    ProviderUnavailable,
    classify_media_errors,
    error_payload,
)
from .minimax import MiniMaxProvider
from .models import ModelsProvider

_PROVIDERS: Dict[str, MediaProvider] = {
    "models": ModelsProvider(),
    "minimax": MiniMaxProvider(),
}

# 新 provider 模块在此注册即可接入优先级链（媒体库配置面板中可调序）
PROVIDER_NAMES = list(_PROVIDERS)


def get_provider(name: str) -> MediaProvider | None:
    return _PROVIDERS.get(name)


def provider_status() -> List[Dict[str, Any]]:
    """各 provider 的能力清单与配置状态（供 HTTP 路由/面板展示与排障）。"""
    status: List[Dict[str, Any]] = []
    for name, provider in _PROVIDERS.items():
        caps = {}
        for cap in sorted(provider.capabilities):
            try:
                caps[cap] = provider.is_configured(cap)
            except Exception:
                caps[cap] = False
        status.append({
            "name": name,
            "capabilities": sorted(provider.capabilities),
            "configured": caps,
        })
    return status


async def run_capability(capability: str, label: str, provider: str = "auto", **kwargs: Any) -> Dict[str, Any]:
    """按优先级链执行媒体能力，返回结果 dict（成功含 success=True，失败含 error）。"""
    if provider and provider != "auto":
        if provider not in _PROVIDERS:
            return error_payload(
                f"未知 provider: {provider}",
                cause=ErrorCause.PARAM, retryable=False,
                hint=f"可选: auto / {' / '.join(PROVIDER_NAMES)}",
            )
        impl = _PROVIDERS[provider]
        if capability not in impl.capabilities:
            # 显式指定了不支持该能力的 provider：参数问题，直接告知谁支持
            supporters = [n for n, p in _PROVIDERS.items() if capability in p.capabilities]
            return error_payload(
                f"provider '{provider}' 不支持能力 '{capability}'",
                cause=ErrorCause.PARAM, retryable=False,
                hint=f"该能力可用 provider: {' / '.join(supporters) or '无'}；"
                     f"或改用 provider=auto 按优先级链自动路由",
            )
        chain = [provider]
    else:
        chain = provider_chain(capability)

    errors: Dict[str, str] = {}
    skipped: Dict[str, str] = {}

    for name in chain:
        impl = _PROVIDERS.get(name)
        if impl is None:
            skipped[name] = "provider 未注册"
            continue
        if capability not in impl.capabilities:
            skipped[name] = "不支持该能力"
            continue
        try:
            if not impl.is_configured(capability):
                skipped[name] = "凭据/模型未配置"
                continue
        except Exception:
            skipped[name] = "可用性检查失败"
            continue
        try:
            result = await impl.run(capability, **kwargs)
            result.setdefault("provider", name)
            result.setdefault("success", True)
            if errors:
                # 主链路失败后降级成功：附带失败原因，便于 AI 感知主链路健康状况
                result["fallback_from"] = list(errors)
                result["primary_error"] = "; ".join(
                    f"{k}: {v}" for k, v in errors.items()
                )[:200]
            return result
        except (CapabilityNotSupported, ProviderUnavailable) as exc:
            skipped[name] = str(exc)[:200]
            continue
        except NotImplementedError as exc:
            skipped[name] = f"协议不支持: {exc}"[:200]
            continue
        except Exception as exc:
            detail = str(exc).strip() or type(exc).__name__
            errors[name] = detail[:200]
            if isinstance(exc, ModelChainError):
                errors.update(exc.model_errors)
            log(f"{label} provider '{name}' 调用失败，尝试下一 provider: {detail}", "WARNING", tag="媒体")
            continue

    if not errors and skipped:
        return error_payload(
            f"{label}不可用：优先级链 {chain} 上的 provider 均未配置或不支持该能力",
            cause=ErrorCause.CONFIG, retryable=False,
            hint="请在媒体库配置面板调整 provider 优先级，或补齐对应凭据/模型配置",
            skipped=skipped,
        )
    cause, retryable, hint = classify_media_errors(errors)
    return error_payload(
        f"{label}失败：所有 provider 均调用失败",
        cause=cause, retryable=retryable, hint=hint,
        errors=errors, skipped=skipped,
    )
