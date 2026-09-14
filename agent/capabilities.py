"""能力提供者路由框架 — 各领域能力（视觉生成/声音合成等）的统一分发骨架。

分层约定：
- 核心层定义 ``CapabilityProvider`` 协议与 ``CapabilityRouter`` 路由；
- 内部模型利用：各领域内置名为 ``models`` 的提供者，桥接模型配置
  （llm_clients.json）中对应类型的模型优先级链；
- 第三方组件：实体经 entities._sdk 的注册桥把同名协议的提供者挂进路由，
  未显式配置优先级链时自动加入链尾（即插即用）；

解析语义：
- 工具参数 provider=auto（默认）按优先级链依次尝试，失败降级并聚合归因；
- 显式指定 provider 不做隐式回退，不可用即报明原因；
- 优先级链经配置键（JSON 字典 {能力: [provider 名]}）调整，留空/缺失时
  为默认链 + 已注册组件（注册顺序）。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Protocol, Tuple, runtime_checkable

from core.log import log
from core.tool_errors import ErrorCause


class ProviderUnavailable(Exception):
    """提供者未配置/不可用，路由器跳过并尝试下一提供者。"""


class CapabilityNotSupported(Exception):
    """提供者不支持该能力/操作，路由器跳过并尝试下一提供者。"""


class ProviderChainError(RuntimeError):
    """模型链全部失败，携带逐模型错误明细（路由器聚合归因用）。"""

    def __init__(self, message: str, model_errors: Dict[str, str]) -> None:
        super().__init__(message)
        self.model_errors = model_errors


@runtime_checkable
class CapabilityProvider(Protocol):
    """能力提供者协议：声明能力集合，按能力执行调用。"""

    name: str
    capabilities: frozenset

    def is_configured(self, capability: str) -> bool:
        """该能力所需的凭据/模型是否就绪。未就绪时路由器跳过本提供者。"""
        ...

    def status_details(self, capability: str) -> Dict[str, Any]:
        """能力状态的附加诊断信息（供面板展示与排障），默认无。"""
        ...

    async def run(self, capability: str, **kwargs: Any) -> Dict[str, Any]:
        """执行能力调用，成功返回产物 dict；不可用/不支持抛对应异常。"""
        ...


def error_payload(
    message: str,
    *,
    cause: "ErrorCause | None" = None,
    hint: str = "",
    retryable: "bool | None" = None,
    **context: Any,
) -> Dict[str, Any]:
    """构造与 core.tool_errors.tool_error 同构的错误 dict（路由内部 dict 流使用）。"""
    payload: Dict[str, Any] = {"error": message}
    if cause is not None:
        payload["cause"] = cause.value
    if hint:
        payload["hint"] = hint
    if retryable is not None:
        payload["retryable"] = retryable
    for key, value in context.items():
        if value is not None:
            payload[key] = value
    return payload


def classify_provider_errors(errors: Dict[str, str]) -> Tuple[ErrorCause, bool, str]:
    """根据各提供者/模型错误详情推断整体归因，让 AI 拿到可决策的 cause/hint。"""
    detail = " ".join(errors.values()).lower()
    if any(k in detail for k in ("http 401", "http 403", "(1004)", "[1004]", "(2049)", "[2049]",
                                 "invalid api key", "unauthorized")):
        return (ErrorCause.CONFIG, False, "API Key 无效或无权限，请检查对应提供者的密钥配置")
    if any(k in detail for k in ("http 402", "(1008)", "[1008]", "余额", "insufficient")):
        return (ErrorCause.CONFIG, False, "账户余额不足，请充值后重试")
    if any(k in detail for k in ("http 422", "(1026)", "[1026]", "(1027)", "[1027]", "敏感")):
        return (ErrorCause.PARAM, False, "内容触发平台敏感审核，请调整提示词/素材后重试")
    if any(k in detail for k in ("无法下载", "已过期", "expired")):
        return (ErrorCause.NOT_FOUND, False, "媒体链接已过期，无法恢复")
    if "http 429" in detail:
        return (ErrorCause.NETWORK, True, "触发平台限流，可稍后重试")
    if "timeout" in detail or "超时" in detail:
        return (ErrorCause.TIMEOUT, True, "可稍后重试")
    return (ErrorCause.NETWORK, True, "可稍后重试，或在配置中调整该能力的提供者优先级")


class CapabilityRouter:
    """能力路由器：提供者注册表 + 配置化优先级链 + 失败降级分发。

    Args:
        config_key: 优先级链配置键（JSON 字典 {能力名: [提供者名]}）；
            某能力未配置/为空时取默认链并自动追加已注册组件。
        default_chain: 各能力兜底链（通常为 ["models"]，内部模型利用）。
        log_tag: 日志标签。
    """

    def __init__(self, *, config_key: str, default_chain: List[str], log_tag: str) -> None:
        self._providers: Dict[str, CapabilityProvider] = {}
        self._config_key = config_key
        self._default_chain = list(default_chain)
        self._log_tag = log_tag

    # ------------------------------------------------------------------
    # 注册表
    # ------------------------------------------------------------------

    def register(self, provider: CapabilityProvider) -> None:
        """注册提供者（同名覆盖，组件热重载安全）。"""
        name = getattr(provider, "name", "")
        if not name:
            raise ValueError("能力提供者缺少 name")
        caps = getattr(provider, "capabilities", None)
        if not caps:
            raise ValueError(f"能力提供者 {name} 未声明 capabilities")
        self._providers[name] = provider
        log(f"能力提供者已注册: {name} ({sorted(caps)})", "DEBUG", tag=self._log_tag)

    def unregister(self, name: str) -> None:
        self._providers.pop(name, None)

    def get(self, name: str) -> Optional[CapabilityProvider]:
        return self._providers.get(name)

    def names(self) -> List[str]:
        return list(self._providers)

    # ------------------------------------------------------------------
    # 优先级链
    # ------------------------------------------------------------------

    def chain(self, capability: str) -> List[str]:
        """指定能力的生效优先级链。

        配置显式给出非空链时严格按配置（可用于排除组件）；否则为默认链
        + 声明了该能力且未列入的已注册提供者（组件自动入链，即插即用）。
        """
        from core.config import ConfigManager

        configured = ConfigManager.get(self._config_key, None)
        if isinstance(configured, dict):
            chain = configured.get(capability)
            if isinstance(chain, list) and chain:
                return [str(p) for p in chain]
        base = list(self._default_chain)
        for name, provider in self._providers.items():
            if name not in base and capability in getattr(provider, "capabilities", ()):
                base.append(name)
        return base

    def status(self, capabilities: List[str]) -> Dict[str, Any]:
        """各提供者的能力配置状态 + 各能力生效链（面板展示与排障）。"""
        providers: List[Dict[str, Any]] = []
        for name, provider in self._providers.items():
            caps: Dict[str, bool] = {}
            details: Dict[str, Any] = {}
            for cap in sorted(provider.capabilities):
                try:
                    caps[cap] = provider.is_configured(cap)
                except Exception:
                    caps[cap] = False
                try:
                    detail = provider.status_details(cap)
                except Exception:
                    detail = {}
                if detail:
                    details[cap] = detail
            providers.append({
                "name": name,
                "capabilities": sorted(provider.capabilities),
                "configured": caps,
                "details": details,
            })
        return {
            "providers": providers,
            "chains": {cap: self.chain(cap) for cap in capabilities},
        }

    # ------------------------------------------------------------------
    # 分发
    # ------------------------------------------------------------------

    async def run(
        self,
        capability: str,
        label: str,
        provider: str = "auto",
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """按优先级链执行能力调用，返回结果 dict（成功含 success=True，失败含 error）。"""
        if provider and provider != "auto":
            impl = self._providers.get(provider)
            if impl is None:
                return error_payload(
                    f"未知提供者: {provider}",
                    cause=ErrorCause.PARAM, retryable=False,
                    hint=f"可选: auto / {' / '.join(self._providers)}",
                )
            if capability not in impl.capabilities:
                supporters = [n for n, p in self._providers.items() if capability in p.capabilities]
                return error_payload(
                    f"提供者 '{provider}' 不支持能力 '{capability}'",
                    cause=ErrorCause.PARAM, retryable=False,
                    hint=f"该能力可用提供者: {' / '.join(supporters) or '无'}；"
                         f"或改用 provider=auto 按优先级链自动路由",
                )
            chain = [provider]
        else:
            chain = self.chain(capability)

        errors: Dict[str, str] = {}
        skipped: Dict[str, str] = {}

        for name in chain:
            impl = self._providers.get(name)
            if impl is None:
                skipped[name] = "提供者未注册"
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
                if isinstance(exc, ProviderChainError):
                    errors.update(exc.model_errors)
                log(f"{label} 提供者 '{name}' 调用失败，尝试下一提供者: {detail}",
                    "WARNING", tag=self._log_tag)
                continue

        if not errors and skipped:
            return error_payload(
                f"{label}不可用：优先级链 {chain} 上的提供者均未配置或不支持该能力",
                cause=ErrorCause.CONFIG, retryable=False,
                hint="请调整该能力的提供者优先级配置，或补齐对应凭据/模型配置",
                skipped=skipped,
            )
        cause, retryable, hint = classify_provider_errors(errors)
        return error_payload(
            f"{label}失败：所有提供者均调用失败",
            cause=cause, retryable=retryable, hint=hint,
            errors=errors, skipped=skipped,
        )
