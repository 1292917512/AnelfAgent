"""
LLMManager — 多 LLM 客户端管理器（供应商-模型层级）。

从 config/llm_clients.json 加载/保存配置，
供应商共享 base_url / api_key，模型独立参数。
按类型的优先级列表决定模型选择顺序。
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import litellm

from agent.llm.llm_client import LLMClient, LLMClientConfig, ModelType
from agent.llm.types import ChatResult
from core.entity import BaseEntity, EntityType
from core.log import info, warning, error

_CONFIG_PATH = "config/llm_clients.json"


@dataclass
class ProviderConfig:
    """供应商级别配置。"""

    id: str
    name: str = ""
    base_url: str = ""
    api_key: str = ""
    api_type: str = "openai"
    proxy_url: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name or self.id,
            "base_url": self.base_url,
            "api_key": self.api_key,
            "api_type": self.api_type,
            "proxy_url": self.proxy_url,
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
                        temperature=mdata.get("temperature", 0.7),
                        top_p=mdata.get("top_p", 1.0),
                        max_tokens=mdata.get("max_tokens", 4096),
                        frequency_penalty=mdata.get("frequency_penalty", 0.0),
                        presence_penalty=mdata.get("presence_penalty", 0.0),
                        timeout=mdata.get("timeout", 120.0),
                        proxy_url=prov.proxy_url,
                        supports_vision=mdata.get("supports_vision", False),
                        supports_tools=mdata.get("supports_tools", True),
                        vision_format=mdata.get("vision_format", "base64"),
                        model_types=mdata.get("model_types", ["chat"]),
                        provider_id=prov.id,
                        supports_reasoning=mdata.get("supports_reasoning", False),
                        extra_params=mdata.get("extra_params", {}),
                    )
                    self._clients[mid] = LLMClient(config=cfg)
            except Exception as exc:
                warning(f"跳过无效供应商配置 {pdata.get('id', '?')}: {exc}", tag="模型")

        self._type_priorities = {
            k: [mid for mid in v if mid in self._clients]
            for k, v in data.get("type_priorities", {}).items()
        }
        self._ensure_priorities_complete()

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

        流程：主模型重试 → 按优先级尝试回退模型 → 全部失败则抛出异常。
        """
        primary = client or self.get_default()
        primary_name = primary.config.name

        last_exc: Optional[Exception] = None
        for attempt in range(max_retries + 1):
            try:
                result = await asyncio.wait_for(
                    primary.chat(
                        messages, options=options,
                        tools=tools, tool_choice=tool_choice,
                    ),
                    timeout=timeout,
                )
                return result
            except asyncio.TimeoutError:
                last_exc = asyncio.TimeoutError(f"LLM 调用超时 ({timeout}s)")
                if attempt < max_retries:
                    warning(
                        f"LLM [{primary_name}] 超时，重试 {attempt + 1}/{max_retries}",
                        tag="模型",
                    )
            except (litellm.AuthenticationError, litellm.PermissionDeniedError) as exc:
                warning(f"LLM [{primary_name}] 认证/权限错误，跳过重试: {exc}", tag="模型")
                last_exc = exc
                break
            except litellm.ContextWindowExceededError as exc:
                warning(f"LLM [{primary_name}] 上下文超限: {exc}", tag="模型")
                last_exc = exc
                break
            except litellm.RateLimitError as exc:
                last_exc = exc
                if attempt < max_retries:
                    warning(f"LLM [{primary_name}] 限速，重试 {attempt + 1}/{max_retries}", tag="模型")
                    await asyncio.sleep(min(2 ** attempt, 8))
            except Exception as exc:
                last_exc = exc
                if attempt < max_retries:
                    warning(
                        f"LLM [{primary_name}] 调用失败 ({type(exc).__name__}: {exc})，"
                        f"重试 {attempt + 1}/{max_retries}",
                        tag="模型",
                    )

        fallbacks = self.get_fallback_chat_clients(
            exclude=primary_name, require_tools=bool(tools),
        )
        if fallbacks:
            info(f"开始模型回退，尝试 {len(fallbacks)} 个候选", tag="模型")
            for fb in fallbacks:
                try:
                    info(
                        f"尝试回退: {fb.config.name} ({fb.config.model})",
                        tag="模型",
                    )
                    result = await asyncio.wait_for(
                        fb.chat(
                            messages, options=options,
                            tools=tools, tool_choice=tool_choice,
                        ),
                        timeout=timeout,
                    )
                    info(f"回退成功: {fb.config.name}", tag="模型")
                    return result
                except (litellm.AuthenticationError, litellm.PermissionDeniedError) as exc:
                    warning(f"回退模型 {fb.config.name} 认证错误: {exc}", tag="模型")
                    last_exc = exc
                except Exception as exc:
                    warning(
                        f"回退模型 {fb.config.name} 失败: {exc}", tag="模型",
                    )
                    last_exc = exc

        raise last_exc  # type: ignore[misc]

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
    ) -> ProviderConfig:
        if pid in self._providers:
            warning(f"供应商 '{pid}' 已存在，将被覆盖", tag="模型")
        prov = ProviderConfig(
            id=pid, name=name or pid, base_url=base_url,
            api_key=api_key, api_type=api_type, proxy_url=proxy_url,
        )
        self._providers[pid] = prov
        if pid not in self._provider_order:
            self._provider_order.append(pid)
        return prov

    def update_provider(self, pid: str, **kwargs: Any) -> bool:
        prov = self._providers.get(pid)
        if prov is None:
            return False
        for k, v in kwargs.items():
            if hasattr(prov, k) and k != "id":
                setattr(prov, k, v)
        # 同步更新该供应商下所有模型的连接参数
        for client in self._clients.values():
            if client.config.provider_id != pid:
                continue
            client.update_config(
                base_url=prov.base_url,
                api_key=prov.api_key,
                api_type=prov.api_type,
                proxy_url=prov.proxy_url,
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

        cfg = LLMClientConfig(
            name=model_id,
            base_url=prov.base_url,
            api_key=prov.api_key,
            model=model,
            api_type=prov.api_type,
            proxy_url=prov.proxy_url,
            provider_id=provider_id,
            **{k: v for k, v in kwargs.items() if hasattr(LLMClientConfig, k)},
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

    def get_default(self) -> LLMClient:
        """按 chat 优先级列表顺序返回第一个可用的对话模型。

        优先选择 supports_tools 的模型，若无则回退到任意 chat 模型。
        """
        chat_prio = self._type_priorities.get("chat", [])
        for mid in chat_prio:
            client = self._clients.get(mid)
            if client is not None and client.config.supports_tools:
                self._default_chat = mid
                return client
        for mid in chat_prio:
            client = self._clients.get(mid)
            if client is not None:
                self._default_chat = mid
                warning(f"默认模型 '{mid}' 不支持工具调用，功能将受限", tag="模型")
                return client
        if self._clients:
            first = next(iter(self._clients.values()))
            self._default_chat = first.config.name
            return first
        dummy = LLMClient(config=LLMClientConfig(name="_empty"))
        warning("无可用 LLM 客户端，返回空客户端", tag="模型")
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
        client.update_config(**{k: v for k, v in kwargs.items()
                                if k not in ("provider_id", "base_url", "api_key", "api_type", "proxy_url")})
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

    def get_type_priorities(self) -> Dict[str, List[Dict[str, Any]]]:
        """返回所有类型的优先级列表（含模型详情）。

        is_default 仅在 chat 类型中标记（排在首位的即为默认对话模型），
        其他类型统一按列表顺序决定优先级，无独立的"默认"概念。
        """
        default_chat = self.get_default().config.name if self._clients else ""
        result: Dict[str, List[Dict[str, Any]]] = {}
        for mt, mids in self._type_priorities.items():
            items: List[Dict[str, Any]] = []
            for mid in mids:
                client = self._clients.get(mid)
                if not client:
                    continue
                items.append({
                    "id": mid,
                    "model": client.config.model,
                    "provider_id": client.config.provider_id,
                    "provider_name": self._providers.get(
                        client.config.provider_id, ProviderConfig(id="")
                    ).name,
                    "is_default": mt == "chat" and mid == default_chat,
                    "supports_vision": client.config.supports_vision,
                    "supports_tools": client.config.supports_tools,
                    "supports_reasoning": client.config.supports_reasoning,
                    "api_type": client.config.api_type,
                })
            result[mt] = items
        return result

    def set_type_priority(self, model_type: str, model_ids: List[str]) -> None:
        """设置某类型的完整优先级顺序。"""
        valid = [mid for mid in model_ids if mid in self._clients]
        current = self._type_priorities.get(model_type, [])
        for mid in current:
            if mid not in valid and mid in self._clients:
                valid.append(mid)
        self._type_priorities[model_type] = valid
        self.save_config()

    def move_model_priority(self, model_type: str, model_id: str, direction: int) -> bool:
        """在类型优先级列表中上移(-1)/下移(+1)。"""
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
        return True

    def get_provider_models(self, provider_id: str) -> List[Dict[str, Any]]:
        """获取指定供应商下的所有模型配置。"""
        default_chat = self.get_default().config.name if self._clients else ""
        result: List[Dict[str, Any]] = []
        for mid, client in self._clients.items():
            if client.config.provider_id != provider_id:
                continue
            d = client.config.to_model_dict()
            d["is_default"] = (mid == default_chat
                               and "chat" in client.config.model_types)
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


# ------------------------------------------------------------------
# 全局单例
# ------------------------------------------------------------------

_manager: Optional[LLMManager] = None


def get_llm_manager(config_path: str = _CONFIG_PATH) -> LLMManager:
    global _manager
    if _manager is None:
        _manager = LLMManager(config_path=config_path)
    return _manager
