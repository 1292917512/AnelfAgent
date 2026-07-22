"""
LLMManager — 多 LLM 客户端管理器（供应商-模型层级）。

从 config/llm_clients.json 加载/保存配置，
供应商共享 base_url / api_key，模型独立参数。
按类型的优先级列表决定模型选择顺序。
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Dict, List, Optional

import litellm

from agent.llm.llm_client import (
    API_TYPES, DEFAULT_TIMEOUT, LLMClient, LLMClientConfig, LLMNotConfiguredError, ModelType,
)
from agent.llm.types import ChatResult
from core.entity import BaseEntity, EntityType
from core.log import info, warning, error
from core.path import ConfigPaths

_CONFIG_PATH = ConfigPaths.LLM_CLIENTS


@dataclass
class ProviderConfig:
    """供应商级别配置。"""

    id: str
    name: str = ""
    base_url: str = ""
    api_key: str = ""
    api_type: str = "openai"
    proxy_url: str = ""
    # 图片生成协议适配器名（见 agent.llm.image_adapters），空表示按 host 自动匹配。
    media_protocol: str = ""

    def __post_init__(self) -> None:
        if not self.id.strip():
            raise ValueError("供应商 id 不能为空")
        if self.api_type not in API_TYPES:
            raise ValueError(f"不支持的 api_type: {self.api_type}")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name or self.id,
            "base_url": self.base_url,
            "api_key": self.api_key,
            "api_type": self.api_type,
            "proxy_url": self.proxy_url,
            "media_protocol": self.media_protocol,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ProviderConfig":
        return cls(
            id=data["id"],
            name=data.get("name", data["id"]),
            base_url=data.get("base_url", ""),
            api_key=data.get("api_key", ""),
            api_type=data.get("api_type", "openai"),
            proxy_url=data.get("proxy_url", ""),
            media_protocol=data.get("media_protocol", ""),
        )


class LLMManager(BaseEntity):
    """管理多个供应商和 LLMClient 实例。

    按 type_priorities 中的顺序选择模型，同类型下排在前面的优先使用。
    """

    _entity_type = EntityType.SERVICE
    _entity_description = "LLM 管理器 — 管理多个大语言模型客户端实例"

    def __init__(self, config_path: str = _CONFIG_PATH) -> None:
        self._config_path = Path(config_path)
        self._providers: Dict[str, ProviderConfig] = {}
        self._provider_order: List[str] = []
        self._clients: Dict[str, LLMClient] = {}
        self._type_priorities: Dict[str, List[str]] = {}
        self._default_chat: str = ""
        self._load_config()
        super().__init__()

    # ------------------------------------------------------------------
    # 配置持久化
    # ------------------------------------------------------------------

    def _load_config(self) -> None:
        if not self._config_path.exists():
            info("LLM 客户端配置不存在，创建空配置", tag="模型")
            self.save_config()
            return
        try:
            data = json.loads(self._config_path.read_text(encoding="utf-8"))
            self._apply_config(data)
            info(
                f"已加载 {len(self._clients)} 个模型 / {len(self._providers)} 个供应商 "
                f"(默认: {self._default_chat})",
                tag="模型",
            )
        except Exception as exc:
            error(f"加载 LLM 配置失败: {exc}", tag="模型")

    def _apply_config(self, data: Dict[str, Any]) -> None:
        self._providers.clear()
        self._provider_order.clear()
        self._clients.clear()
        self._type_priorities.clear()
        self._default_chat = data.get("default_chat", "")

        for pdata in data.get("providers", []):
            try:
                prov = ProviderConfig.from_dict(pdata)
                self._providers[prov.id] = prov
                self._provider_order.append(prov.id)
                for mdata in pdata.get("models", []):
                    mid = mdata.get("id", mdata.get("name", ""))
                    if not mid:
                        continue
                    cfg = LLMClientConfig(
                        name=mid,
                        base_url=prov.base_url,
                        api_key=prov.api_key,
                        model=mdata.get("model", ""),
                        api_type=prov.api_type,
                        temperature=mdata.get("temperature"),
                        top_p=mdata.get("top_p"),
                        max_tokens=mdata.get("max_tokens"),
                        frequency_penalty=mdata.get("frequency_penalty", 0.0),
                        presence_penalty=mdata.get("presence_penalty", 0.0),
                        timeout=mdata.get("timeout", DEFAULT_TIMEOUT),
                        proxy_url=prov.proxy_url,
                        supports_vision=mdata.get("supports_vision", False),
                        supports_tools=mdata.get("supports_tools", True),
                        supports_forced_tool_choice=mdata.get("supports_forced_tool_choice", True),
                        vision_format=mdata.get("vision_format", "base64"),
                        model_types=mdata.get("model_types", ["chat"]),
                        provider_id=prov.id,
                        supports_reasoning=mdata.get("supports_reasoning", False),
                        context_window=mdata.get("context_window", 0),
                        request_params=mdata.get("request_params", {}),
                        extra_body=mdata.get("extra_body", {}),
                        extra_params=mdata.get("extra_params", {}),
                        chat_protocol=mdata.get("chat_protocol", "chat_completions"),
                        media_protocol=mdata.get("media_protocol", prov.media_protocol),
                    )
                    self._clients[mid] = LLMClient(config=cfg)
            except Exception as exc:
                warning(f"跳过无效供应商配置 {pdata.get('id', '?')}: {exc}", tag="模型")

        self._type_priorities = {
            k: [mid for mid in v if mid in self._clients]
            for k, v in data.get("type_priorities", {}).items()
        }
        self._ensure_priorities_complete()
        self._register_unknown_models()

    def _ensure_priorities_complete(self) -> None:
        """确保所有模型都出现在对应类型的优先级列表中。

        supports_vision=True 的 chat 模型会自动加入 vision 优先级列表，
        vision 列表独立于 chat 列表，供视觉任务使用。
        """
        for mid, client in self._clients.items():
            for mt in client.config.model_types:
                plist = self._type_priorities.setdefault(mt, [])
                if mid not in plist:
                    plist.append(mid)
            if client.config.supports_vision and "chat" in client.config.model_types:
                vision_list = self._type_priorities.setdefault("vision", [])
                if mid not in vision_list:
                    vision_list.append(mid)

    def _register_unknown_models(self) -> None:
        """将 litellm 未收录的自定义模型注册到模型信息表，使 get_model_info 可查。

        通过公开 API register_model 注册（而非直接改写 model_cost 字典），
        由 litellm 负责失效其内部的小写索引与 get_model_info LRU 缓存。
        """
        custom_models: Dict[str, Dict[str, Any]] = {}
        for client in self._clients.values():
            cfg = client.config
            model_key = cfg.litellm_model
            # register_model 会以去前缀名入库，两种形式都已存在则跳过，
            # 避免重复注册或将零值合并进内置同名条目
            if model_key in litellm.model_cost or model_key.split("/", 1)[-1] in litellm.model_cost:
                continue
            entry: Dict[str, Any] = {
                "input_cost_per_token": 0,
                "output_cost_per_token": 0,
                "cache_creation_input_token_cost": 0,
                "cache_read_input_token_cost": 0,
                "litellm_provider": model_key.split("/", 1)[0],
                "mode": cfg.model_types[0] if cfg.model_types else "chat",
                "supports_function_calling": cfg.supports_tools,
                "supports_vision": cfg.supports_vision,
            }
            # 上下文窗口是模型属性，输出预算仅在显式配置时声明
            if cfg.context_window:
                entry["max_tokens"] = cfg.context_window
                entry["max_input_tokens"] = cfg.context_window
            if cfg.max_tokens:
                entry.setdefault("max_tokens", cfg.max_tokens)
                entry["max_output_tokens"] = cfg.max_tokens
            custom_models[model_key] = entry
        if not custom_models:
            return
        litellm.register_model(custom_models)
        info(f"已注册 {len(custom_models)} 个自定义模型到 litellm 模型信息表", tag="模型")

    def save_config(self) -> bool:
        try:
            self._config_path.parent.mkdir(parents=True, exist_ok=True)
            providers_out: List[Dict[str, Any]] = []
            for pid in self._provider_order:
                prov = self._providers.get(pid)
                if not prov:
                    continue
                models_out: List[Dict[str, Any]] = []
                for mid, client in self._clients.items():
                    if client.config.provider_id != pid:
                        continue
                    models_out.append(client.config.to_model_dict())
                pd = prov.to_dict()
                pd["models"] = models_out
                providers_out.append(pd)

            out = {
                "providers": providers_out,
                "type_priorities": self._type_priorities,
                "default_chat": self._default_chat,
            }
            self._config_path.write_text(
                json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8",
            )
            info("LLM 配置已保存", tag="模型")
            return True
        except Exception as exc:
            error(f"保存 LLM 配置失败: {exc}", tag="模型")
            return False

    # ------------------------------------------------------------------
    # 按类型/能力查找（按 type_priorities 顺序）
    # ------------------------------------------------------------------

    def _iter_by_type(self, model_type: ModelType) -> List[str]:
        """返回某类型的模型 ID 优先级列表。"""
        return self._type_priorities.get(model_type.value, [])

    def get_by_type(
        self,
        model_type: ModelType,
        *,
        require_tools: bool = False,
        require_vision: bool = False,
    ) -> Optional[LLMClient]:
        """按类型获取最高优先级的客户端。"""
        for mid in self._iter_by_type(model_type):
            client = self._clients.get(mid)
            if not client:
                continue
            if require_tools and not client.config.supports_tools:
                continue
            if require_vision and not client.config.supports_vision:
                continue
            return client
        return None

    def get_all_by_type(self, model_type: ModelType) -> List[LLMClient]:
        """获取指定类型的所有客户端（按优先级）。"""
        result: List[LLMClient] = []
        for mid in self._iter_by_type(model_type):
            client = self._clients.get(mid)
            if client:
                result.append(client)
        return result

    def get_chat_client(self, *, require_tools: bool = False) -> Optional[LLMClient]:
        return self.get_by_type(ModelType.CHAT, require_tools=require_tools)

    def get_vision_client(self) -> Optional[LLMClient]:
        """获取最高优先级的视觉模型（按 vision 优先级列表顺序）。"""
        return self.get_by_type(ModelType.VISION)

    def get_embedding_client(self) -> Optional[LLMClient]:
        return self.get_by_type(ModelType.EMBEDDING)

    def get_rerank_client(self) -> Optional[LLMClient]:
        return self.get_by_type(ModelType.RERANK)

    def get_media_client(self, model_type: str = "") -> Optional["MediaClient"]:
        """按模型类型获取对应凭据的 MediaClient。

        指定 model_type 时从该类型的优先级列表取凭据；
        未指定时按 asr → tts → video → rerank 顺序取首个可用。
        """
        from agent.llm.media_client import MediaClient
        search_types = [model_type] if model_type else ["asr", "tts", "video", "rerank"]
        for mt in search_types:
            for mid in self._type_priorities.get(mt, []):
                client = self._clients.get(mid)
                if not client:
                    continue
                return MediaClient(
                    base_url=client.config.base_url,
                    api_key=client.config.api_key,
                    timeout=client.config.timeout,
                    proxy_url=client.config.effective_proxy,
                    image_protocol=client.config.media_protocol,
                )
        return None

    def get_image_gen_client(self) -> Optional["MediaClient"]:
        """获取图片生成专用 MediaClient（使用 image_gen 模型的凭据）。"""
        return self.get_media_client("image_gen")

    def get_asr_model(self) -> Optional[str]:
        return self._find_model_by_type("asr")

    def get_tts_model(self) -> Optional[str]:
        return self._find_model_by_type("tts")

    def get_video_model(self) -> Optional[str]:
        return self._find_model_by_type("video")

    def get_rerank_model(self) -> Optional[str]:
        return self._find_model_by_type("rerank")

    def get_image_gen_model(self) -> Optional[str]:
        return self._find_model_by_type("image_gen")

    def get_image_edit_client(self) -> Optional["MediaClient"]:
        """获取图片编辑专用 MediaClient（使用 image_edit 模型的凭据）。"""
        return self.get_media_client("image_edit")

    def get_image_edit_model(self) -> Optional[str]:
        return self._find_model_by_type("image_edit")

    def iter_media_for_type(self, model_type: str) -> List[tuple]:
        """返回指定类型的所有 (model_name, MediaClient) 对，按优先级排序。

        用于带回退的媒体工具调用：依次尝试每个模型，第一个成功的即返回。
        """
        from agent.llm.media_client import MediaClient
        result: List[tuple] = []
        for mid in self._type_priorities.get(model_type, []):
            client = self._clients.get(mid)
            if not client:
                continue
            mc = MediaClient(
                base_url=client.config.base_url,
                api_key=client.config.api_key,
                timeout=client.config.timeout,
                proxy_url=client.config.effective_proxy,
                image_protocol=client.config.media_protocol,
            )
            result.append((client.config.model, mc))
        return result

    def _find_model_by_type(self, model_type: str, keyword: str = "") -> Optional[str]:
        priority = self._type_priorities.get(model_type, [])
        if keyword:
            for mid in priority:
                client = self._clients.get(mid)
                if not client:
                    continue
                if keyword.lower() in client.config.model.lower() or keyword.lower() in mid.lower():
                    return client.config.model
        for mid in priority:
            client = self._clients.get(mid)
            if client:
                return client.config.model
        return None

    def get_fallback_chat_clients(
        self,
        *,
        exclude: Optional[str] = None,
        require_tools: bool = False,
        require_vision: bool = False,
    ) -> List[LLMClient]:
        """获取可用于回退的 chat 客户端列表（按优先级）。"""
        result: List[LLMClient] = []
        for mid in self._iter_by_type(ModelType.CHAT):
            if mid == exclude:
                continue
            client = self._clients.get(mid)
            if not client:
                continue
            if require_tools and not client.config.supports_tools:
                continue
            if require_vision and not client.config.supports_vision:
                continue
            result.append(client)
        return result

    async def chat_with_fallback(
        self,
        messages: List[Dict[str, Any]],
        *,
        options: Optional[Dict[str, Any]] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: Optional[Any] = None,
        client: Optional[LLMClient] = None,
        max_retries: int = 2,
        timeout: float = 120.0,
    ) -> ChatResult:
        """带重试和模型回退的统一聊天调用。

        所有候选共享总超时预算，避免模型级 timeout 与外层 timeout 叠加失控。
        """
        primary = client or self.get_default()
        candidates = [primary, *self.get_fallback_chat_clients(
            exclude=primary.config.name,
            require_tools=bool(tools),
            require_vision=self._messages_contain_images(messages),
        )]
        deadline = asyncio.get_running_loop().time() + timeout
        last_exc: Optional[Exception] = None
        index = 0
        while index < len(candidates):
            candidate = candidates[index]
            if index:
                info(
                    f"尝试回退: {candidate.config.name} ({candidate.config.model})",
                    tag="模型",
                )
            try:
                result = await self._chat_candidate(
                    candidate,
                    messages,
                    options=options,
                    tools=tools,
                    tool_choice=tool_choice,
                    max_retries=max_retries,
                    deadline=deadline,
                )
                if index:
                    info(f"回退成功: {candidate.config.name}", tag="模型")
                return result
            except LLMNotConfiguredError:
                raise
            except Exception as exc:
                last_exc = exc
                warning(
                    f"LLM [{candidate.config.name}] 最终失败: "
                    f"{self._safe_error(exc, candidate)}",
                    tag="模型",
                )
                if asyncio.get_running_loop().time() >= deadline:
                    break
                # 上下文超限：窗口不大于当前失败者的候选必然同样溢出，
                # 跳过它们直达更大窗口候选（无则快速失败，交由调用方压缩重试）
                from agent.llm.resilience import next_fallback_index
                nxt = next_fallback_index(exc, candidate, candidates, index + 1)
                if nxt is None:
                    break
                if nxt > index + 1:
                    skipped = [c.config.name for c in candidates[index + 1:nxt]]
                    info(
                        f"跳过窗口不更大的候选: {', '.join(skipped)}",
                        tag="模型",
                    )
                index = nxt
                continue
            index += 1

        if last_exc is not None:
            raise last_exc
        raise RuntimeError("没有可用的 LLM 候选模型")

    async def _chat_candidate(
        self,
        client: LLMClient,
        messages: List[Dict[str, Any]],
        *,
        options: Optional[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]],
        tool_choice: Optional[Any],
        max_retries: int,
        deadline: float,
    ) -> ChatResult:
        """在共享截止时间内调用单个候选，并按错误分类自适应重试。

        重试策略由 error_classifier 驱动：
        - 不可重试错误（auth/参数/上下文超限/模型不存在）立即抛出
        - 限流错误使用更长基础退避 + 抖动
        - 其余瞬态错误使用标准指数退避 + 抖动
        """
        from agent.llm.resilience import ErrorCategory, classify_llm_error
        from agent.llm.retry import jittered_backoff

        name = client.config.name
        for attempt in range(max(0, max_retries) + 1):
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise asyncio.TimeoutError("LLM 调用总超时")
            try:
                result = await asyncio.wait_for(
                    client.chat(
                        messages,
                        options=options,
                        tools=tools,
                        tool_choice=tool_choice,
                    ),
                    timeout=min(remaining, client.config.timeout),
                )
                if result.finish_reason == "error":
                    raise RuntimeError("LLM 返回了无有效 choices/message 的响应")
                return result
            except LLMNotConfiguredError:
                raise
            except Exception as exc:
                classified = classify_llm_error(exc)
                if not classified.retryable or attempt >= max_retries:
                    raise
                # 限流：更长基础退避；其余瞬态错误：标准指数退避（均带抖动）
                if classified.category == ErrorCategory.RATE_LIMIT:
                    delay = jittered_backoff(attempt + 1, base_delay=5.0, max_delay=60.0)
                else:
                    delay = jittered_backoff(attempt + 1, base_delay=2.0, max_delay=30.0)
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= delay:
                    raise asyncio.TimeoutError("LLM 重试预算不足") from exc
                warning(
                    f"LLM [{name}] 调用失败 ({classified.category.value})，"
                    f"退避 {delay:.1f}s 后重试 {attempt + 1}/{max_retries}: "
                    f"{type(exc).__name__}: {self._safe_error(exc, client)}",
                    tag="模型",
                )
                await asyncio.sleep(delay)
        raise RuntimeError("LLM 重试流程异常退出")

    @staticmethod
    def _messages_contain_images(messages: List[Dict[str, Any]]) -> bool:
        for message in messages:
            content = message.get("content")
            if isinstance(content, list) and any(
                isinstance(part, dict)
                and part.get("type") in {"image", "image_url", "input_image"}
                for part in content
            ):
                return True
        return False

    @staticmethod
    def _safe_error(exc: Exception, client: LLMClient) -> str:
        """错误消息脱敏：精确替换当前 key（覆盖任意格式的 key 泄漏），
        再走统一脱敏管线兜底（错误体中可能夹带其他凭证/URL 内联凭证）。"""
        message = str(exc)
        api_key = client.config.api_key
        if api_key:
            message = message.replace(api_key, "****")
        from core.sanitizer import is_sanitize_enabled, sanitize_text
        if is_sanitize_enabled():
            message = sanitize_text(message)
        return message

    def get_models_summary(self) -> str:
        """生成所有可用模型及其能力的摘要（供系统提示词注入）。"""
        lines: List[str] = []
        seen: set[str] = set()
        for plist in self._type_priorities.values():
            for mid in plist:
                if mid in seen:
                    continue
                seen.add(mid)
                client = self._clients.get(mid)
                if not client:
                    continue
                caps: List[str] = list(client.config.model_types)
                if client.config.supports_vision:
                    caps.append("视觉理解")
                if client.config.supports_tools:
                    caps.append("工具调用")
                if client.config.supports_reasoning:
                    caps.append("深度思考")
                default_mark = " (当前默认)" if mid == self._default_chat else ""
                lines.append(
                    f"- {mid}{default_mark}: {client.config.model} [{', '.join(caps)}]"
                )
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # 供应商 CRUD
    # ------------------------------------------------------------------

    def list_providers(self) -> List[Dict[str, Any]]:
        result: List[Dict[str, Any]] = []
        for pid in self._provider_order:
            prov = self._providers.get(pid)
            if not prov:
                continue
            model_count = sum(
                1 for c in self._clients.values() if c.config.provider_id == pid
            )
            d = prov.to_dict()
            d["model_count"] = model_count
            result.append(d)
        return result

    def get_provider(self, pid: str) -> Optional[ProviderConfig]:
        return self._providers.get(pid)

    def create_provider(
        self,
        pid: str,
        name: str = "",
        base_url: str = "",
        api_key: str = "",
        api_type: str = "openai",
        proxy_url: str = "",
        media_protocol: str = "",
    ) -> ProviderConfig:
        if pid in self._providers:
            warning(f"供应商 '{pid}' 已存在，将被覆盖", tag="模型")
        prov = ProviderConfig(
            id=pid, name=name or pid, base_url=base_url,
            api_key=api_key, api_type=api_type, proxy_url=proxy_url,
            media_protocol=media_protocol,
        )
        self._providers[pid] = prov
        if pid not in self._provider_order:
            self._provider_order.append(pid)
        return prov

    def update_provider(self, pid: str, **kwargs: Any) -> bool:
        prov = self._providers.get(pid)
        if prov is None:
            return False
        allowed = {k: v for k, v in kwargs.items() if hasattr(prov, k) and k != "id"}
        updated = replace(prov, **allowed)
        self._providers[pid] = updated
        # 同步更新该供应商下所有模型的连接参数
        for client in self._clients.values():
            if client.config.provider_id != pid:
                continue
            client.update_config(
                base_url=updated.base_url,
                api_key=updated.api_key,
                api_type=updated.api_type,
                proxy_url=updated.proxy_url,
                media_protocol=updated.media_protocol,
            )
        self.save_config()
        return True

    def remove_provider(self, pid: str) -> bool:
        if pid not in self._providers:
            return False
        mids_to_remove = [
            mid for mid, c in self._clients.items() if c.config.provider_id == pid
        ]
        for mid in mids_to_remove:
            self._remove_model_internal(mid)
        self._providers.pop(pid)
        if pid in self._provider_order:
            self._provider_order.remove(pid)
        self.save_config()
        info(f"供应商 '{pid}' 及其 {len(mids_to_remove)} 个模型已删除", tag="模型")
        return True

    # ------------------------------------------------------------------
    # 模型 CRUD
    # ------------------------------------------------------------------

    def create_model(
        self,
        provider_id: str,
        model_id: str,
        model: str = "",
        **kwargs: Any,
    ) -> Optional[LLMClient]:
        prov = self._providers.get(provider_id)
        if prov is None:
            warning(f"供应商 '{provider_id}' 不存在", tag="模型")
            return None
        if model_id in self._clients:
            warning(f"模型 '{model_id}' 已存在，将被覆盖", tag="模型")

        kwargs.setdefault("media_protocol", prov.media_protocol)
        cfg = LLMClientConfig(
            name=model_id,
            base_url=prov.base_url,
            api_key=prov.api_key,
            model=model,
            api_type=prov.api_type,
            proxy_url=prov.proxy_url,
            provider_id=provider_id,
            **{
                k: v for k, v in kwargs.items()
                if k in LLMClientConfig.__dataclass_fields__
            },
        )
        client = LLMClient(config=cfg)
        self._clients[model_id] = client
        for mt in cfg.model_types:
            plist = self._type_priorities.setdefault(mt, [])
            if model_id not in plist:
                plist.append(model_id)
        if not self._default_chat and "chat" in cfg.model_types and cfg.supports_tools:
            self._default_chat = model_id
        if "chat" in cfg.model_types and not cfg.supports_tools:
            warning(f"对话模型 '{model_id}' 未启用工具调用（supports_tools=False），不能作为默认对话模型", tag="模型")
        return client

    def get_client(self, name: str) -> Optional[LLMClient]:
        return self._clients.get(name)

    def get_all_names(self) -> List[str]:
        """返回全部已注册模型 ID。"""
        return list(self._clients.keys())

    def resolve_client(self, model: str) -> Optional[LLMClient]:
        """按模型 ID 或原始模型名解析客户端。

        优先级：精确匹配客户端 ID → 精确匹配 config.model →
        在 chat 优先级中寻找同名原始模型。
        """
        name = (model or "").strip()
        if not name:
            return None
        direct = self._clients.get(name)
        if direct is not None:
            return direct
        matches = [
            client for client in self._clients.values()
            if client.config.model == name
        ]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            for mid in self._type_priorities.get("chat", []):
                client = self._clients.get(mid)
                if client is not None and client.config.model == name:
                    return client
            return matches[0]
        return None

    def get_default(self) -> LLMClient:
        """按 chat 优先级列表顺序返回第一个可用的对话模型。

        优先选择 supports_tools 的模型，若无则回退到任意 chat 模型。
        """
        configured = self._clients.get(self._default_chat)
        if (
            configured is not None
            and "chat" in configured.config.model_types
            and configured.config.supports_tools
        ):
            return configured
        chat_prio = self._type_priorities.get("chat", [])
        for mid in chat_prio:
            client = self._clients.get(mid)
            if client is not None and client.config.supports_tools:
                return client
        for mid in chat_prio:
            client = self._clients.get(mid)
            if client is not None:
                warning(f"默认模型 '{mid}' 不支持工具调用，功能将受限", tag="模型")
                return client
        if self._clients:
            first = next(iter(self._clients.values()))
            return first
        dummy = LLMClient(config=LLMClientConfig(name="_empty", base_url=""))
        warning("无可用 LLM 客户端，调用时将返回明确配置错误", tag="模型")
        return dummy

    def set_default(self, name: str) -> bool:
        """将模型移到 chat 优先级列表首位，使其成为默认对话模型。"""
        client = self._clients.get(name)
        if not client:
            warning(f"模型 '{name}' 不存在，无法设为默认", tag="模型")
            return False
        if "chat" in client.config.model_types and not client.config.supports_tools:
            warning(f"模型 '{name}' 不支持工具调用（supports_tools=False），不允许设为默认对话模型", tag="模型")
            return False
        chat_prio = self._type_priorities.setdefault("chat", [])
        if name in chat_prio:
            chat_prio.remove(name)
        chat_prio.insert(0, name)
        self._default_chat = name
        self.save_config()
        info(f"默认对话模型已设为: {name}（已移至 chat 优先级首位）", tag="模型")
        return True

    def remove_model(self, model_id: str) -> bool:
        if model_id not in self._clients:
            return False
        self._remove_model_internal(model_id)
        self.save_config()
        info(f"模型 '{model_id}' 已删除", tag="模型")
        return True

    def _remove_model_internal(self, model_id: str) -> None:
        self._clients.pop(model_id, None)
        for plist in self._type_priorities.values():
            if model_id in plist:
                plist.remove(model_id)
        if self._default_chat == model_id:
            chat_prio = self._type_priorities.get("chat", [])
            self._default_chat = chat_prio[0] if chat_prio else ""

    def update_model(self, model_id: str, **kwargs: Any) -> bool:
        client = self._clients.get(model_id)
        if client is None:
            return False
        old_types = set(client.config.model_types)
        old_supports_vision = client.config.supports_vision
        allowed = set(LLMClientConfig.__dataclass_fields__) - {
            "provider_id", "base_url", "api_key", "api_type", "proxy_url",
        }
        client.update_config(**{k: v for k, v in kwargs.items() if k in allowed})
        new_types = set(client.config.model_types)
        new_supports_vision = client.config.supports_vision
        removed = old_types - new_types
        added = new_types - old_types
        for mt in removed:
            plist = self._type_priorities.get(mt, [])
            if model_id in plist:
                plist.remove(model_id)
        for mt in added:
            plist = self._type_priorities.setdefault(mt, [])
            if model_id not in plist:
                plist.append(model_id)
        # 同步 vision 优先级列表
        is_chat_model = "chat" in client.config.model_types
        vision_list = self._type_priorities.setdefault("vision", [])
        if new_supports_vision and is_chat_model:
            if model_id not in vision_list:
                vision_list.append(model_id)
        elif not new_supports_vision and old_supports_vision:
            if model_id in vision_list:
                vision_list.remove(model_id)
        self.save_config()
        return True

    def rename_model(self, old_id: str, new_id: str) -> bool:
        if old_id not in self._clients or new_id in self._clients:
            return False
        client = self._clients.pop(old_id)
        client.config.name = new_id
        self._clients[new_id] = client
        for plist in self._type_priorities.values():
            for i, mid in enumerate(plist):
                if mid == old_id:
                    plist[i] = new_id
        if self._default_chat == old_id:
            self._default_chat = new_id
        self.save_config()
        return True

    # ------------------------------------------------------------------
    # 优先级管理
    # ------------------------------------------------------------------

    @staticmethod
    def _query_model_cost(client: LLMClient) -> Dict[str, Any]:
        """查询 litellm 模型价格和上下文窗口信息，查不到返回空值。"""
        try:
            info = LLMClient.get_model_info(client.config.litellm_model)
            if not info:
                return {
                    "input_cost": None,
                    "output_cost": None,
                    "context_window": client.config.context_window or None,
                }
            input_cost = info.get("input_cost_per_token")
            output_cost = info.get("output_cost_per_token")
            return {
                "input_cost": round(input_cost * 1e6, 2) if input_cost else None,
                "output_cost": round(output_cost * 1e6, 2) if output_cost else None,
                "context_window": (
                    client.config.context_window
                    or info.get("max_input_tokens")
                ),
            }
        except Exception:
            return {"input_cost": None, "output_cost": None, "context_window": None}

    def get_type_priorities(self) -> Dict[str, List[Dict[str, Any]]]:
        """返回所有类型的优先级列表（含模型详情和价格信息）。

        is_default 仅在 chat 类型中标记（排在首位的即为默认对话模型），
        其他类型统一按列表顺序决定优先级，无独立的"默认"概念。
        价格单位：$/M tokens（每百万 token 的美元价格）。
        """
        default_chat = self.get_default().config.name if self._clients else ""
        cost_cache: Dict[str, Dict[str, Any]] = {}
        result: Dict[str, List[Dict[str, Any]]] = {}
        for mt, mids in self._type_priorities.items():
            items: List[Dict[str, Any]] = []
            for mid in mids:
                client = self._clients.get(mid)
                if not client:
                    continue
                if mid not in cost_cache:
                    cost_cache[mid] = self._query_model_cost(client)
                provider = self._providers.get(client.config.provider_id)
                items.append({
                    "id": mid,
                    "model": client.config.model,
                    "provider_id": client.config.provider_id,
                    "provider_name": provider.name if provider is not None else "",
                    "is_default": mt == "chat" and mid == default_chat,
                    "supports_vision": client.config.supports_vision,
                    "supports_tools": client.config.supports_tools,
                    "supports_reasoning": client.config.supports_reasoning,
                    "api_type": client.config.api_type,
                    **cost_cache[mid],
                })
            result[mt] = items
        return result

    def set_type_priority(self, model_type: str, model_ids: List[str]) -> None:
        """设置某类型的完整优先级顺序。"""
        old_first = self._chat_first()
        valid = [mid for mid in model_ids if mid in self._clients]
        current = self._type_priorities.get(model_type, [])
        for mid in current:
            if mid not in valid and mid in self._clients:
                valid.append(mid)
        self._type_priorities[model_type] = valid
        self.save_config()
        if model_type == "chat":
            self._auto_switch_chat(old_first)

    def move_model_priority(self, model_type: str, model_id: str, direction: int) -> bool:
        """在类型优先级列表中上移(-1)/下移(+1)。"""
        old_first = self._chat_first() if model_type == "chat" else None
        plist = self._type_priorities.get(model_type, [])
        try:
            idx = plist.index(model_id)
        except ValueError:
            return False
        new_idx = idx + direction
        if new_idx < 0 or new_idx >= len(plist):
            return False
        plist[idx], plist[new_idx] = plist[new_idx], plist[idx]
        self.save_config()
        if model_type == "chat":
            self._auto_switch_chat(old_first)
        return True

    def _chat_first(self) -> Optional[str]:
        """返回当前 chat 优先级列表中第一个支持工具调用的模型 ID。"""
        for mid in self._type_priorities.get("chat", []):
            client = self._clients.get(mid)
            if client and client.config.supports_tools:
                return mid
        return None

    def _auto_switch_chat(self, old_first: Optional[str]) -> None:
        """chat 优先级变化后，若首位模型改变则自动热切换。"""
        new_first = self._chat_first()
        if new_first and new_first != old_first:
            self._default_chat = new_first
            try:
                from services._runtime import get_runtime
                rt = get_runtime()
                client = self._clients.get(new_first)
                if rt and client:
                    rt.switch_llm(client)
                    info(f"chat 优先级首位变更，已热切换至: {new_first}", tag="模型")
            except Exception:
                pass

    def get_provider_models(self, provider_id: str) -> List[Dict[str, Any]]:
        """获取指定供应商下的所有模型配置（含价格信息）。"""
        default_chat = self.get_default().config.name if self._clients else ""
        result: List[Dict[str, Any]] = []
        for mid, client in self._clients.items():
            if client.config.provider_id != provider_id:
                continue
            d = client.config.to_model_dict()
            d["is_default"] = (mid == default_chat
                               and "chat" in client.config.model_types)
            d.update(self._query_model_cost(client))
            result.append(d)
        return result

    # ------------------------------------------------------------------
    # 属性
    # ------------------------------------------------------------------

    @property
    def default_name(self) -> str:
        return self._default_chat

    @property
    def client_count(self) -> int:
        return len(self._clients)

    @property
    def all_model_ids(self) -> List[str]:
        """返回所有已配置模型的 ID 列表。"""
        return list(self._clients.keys())

    async def close(self) -> None:
        """关闭所有客户端持有的网络资源。"""
        clients = list(self._clients.values())
        if clients:
            await asyncio.gather(
                *(client.close() for client in clients),
                return_exceptions=True,
            )


# ------------------------------------------------------------------
# 全局单例
# ------------------------------------------------------------------

_manager: Optional[LLMManager] = None


def get_llm_manager(config_path: str = _CONFIG_PATH) -> LLMManager:
    global _manager
    if _manager is None:
        _manager = LLMManager(config_path=config_path)
    return _manager
