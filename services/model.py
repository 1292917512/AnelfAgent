"""模型管理服务 -- 供应商/模型 CRUD、优先级管理、连接测试。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, List, Optional

from services._runtime import get_runtime

if TYPE_CHECKING:
    from agent.llm.llm_manager import LLMManager


class ModelService:
    _API_KEY_MASK = "****"

    @staticmethod
    def _manager() -> "LLMManager":
        from agent.llm import get_llm_manager
        return get_llm_manager()

    @classmethod
    def _mask_api_key(cls, api_key: str) -> str:
        """返回不可用于鉴权的密钥掩码。"""
        if not api_key:
            return ""
        if len(api_key) <= 8:
            return cls._API_KEY_MASK
        return f"{api_key[:4]}{cls._API_KEY_MASK}{api_key[-4:]}"

    @classmethod
    def _is_masked_api_key(cls, api_key: str) -> bool:
        return cls._API_KEY_MASK in api_key

    # ------------------------------------------------------------------
    # 供应商
    # ------------------------------------------------------------------

    def list_providers(self) -> List[Dict[str, Any]]:
        providers = self._manager().list_providers()
        return [
            {**provider, "api_key": self._mask_api_key(str(provider.get("api_key", "")))}
            for provider in providers
        ]

    def get_provider(self, pid: str) -> Optional[Dict[str, Any]]:
        prov = self._manager().get_provider(pid)
        if prov is None:
            return None
        result = prov.to_dict()
        result["api_key"] = self._mask_api_key(str(result.get("api_key", "")))
        return result

    def add_provider(self, pid: str, **kwargs: Any) -> bool:
        mgr = self._manager()
        if mgr.get_provider(pid):
            return False
        mgr.create_provider(pid, **kwargs)
        mgr.save_config()
        return True

    def update_provider(self, pid: str, **kwargs: Any) -> bool:
        api_key = kwargs.get("api_key")
        if isinstance(api_key, str) and (not api_key or self._is_masked_api_key(api_key)):
            kwargs.pop("api_key")
        return self._manager().update_provider(pid, **kwargs)

    def resolve_provider_api_key(self, provider_id: str, api_key: str) -> str:
        """将界面提交的空值或掩码解析为供应商当前密钥。"""
        if api_key and not self._is_masked_api_key(api_key):
            return api_key
        provider = self._manager().get_provider(provider_id)
        return provider.api_key if provider is not None else ""

    def sanitize_error(self, exc: Exception, *extra_secrets: str) -> str:
        """移除异常文本中的已配置密钥和请求密钥。"""
        message = str(exc)
        secrets = [secret for secret in extra_secrets if secret]
        for provider in self._manager().list_providers():
            api_key = str(provider.get("api_key", ""))
            if api_key:
                secrets.append(api_key)
        for secret in secrets:
            message = message.replace(secret, self._API_KEY_MASK)
        return message

    def remove_provider(self, pid: str) -> bool:
        return self._manager().remove_provider(pid)

    # ------------------------------------------------------------------
    # 模型
    # ------------------------------------------------------------------

    def list_provider_models(self, provider_id: str) -> List[Dict[str, Any]]:
        return self._manager().get_provider_models(provider_id)

    def get_model_config(self, model_id: str) -> Optional[Dict[str, Any]]:
        client = self._manager().get_client(model_id)
        if client is None:
            return None
        cfg = client.config
        d = cfg.to_model_dict()
        d["provider_id"] = cfg.provider_id
        d["base_url"] = cfg.base_url
        d["api_key"] = self._mask_api_key(cfg.api_key)
        d["api_type"] = cfg.api_type
        d["enabled"] = cfg.enabled
        return d

    def add_model(self, provider_id: str, model_id: str, **kwargs: Any) -> bool:
        mgr = self._manager()
        if mgr.get_client(model_id):
            return False
        client = mgr.create_model(provider_id, model_id, **kwargs)
        if client is None:
            return False
        mgr.save_config()
        return True

    def update_model(self, model_id: str, **kwargs: Any) -> bool:
        return self._manager().update_model(model_id, **kwargs)

    def remove_model(self, model_id: str) -> bool:
        return self._manager().remove_model(model_id)

    def rename_model(self, old_id: str, new_id: str) -> bool:
        return self._manager().rename_model(old_id, new_id)

    # ------------------------------------------------------------------
    # 优先级
    # ------------------------------------------------------------------

    def get_type_priorities(self) -> Dict[str, List[Dict[str, Any]]]:
        return self._manager().get_type_priorities()

    def set_type_priority(self, model_type: str, model_ids: List[str]) -> None:
        self._manager().set_type_priority(model_type, model_ids)

    def move_model_priority(self, model_type: str, model_id: str, direction: int) -> bool:
        return self._manager().move_model_priority(model_type, model_id, direction)

    # ------------------------------------------------------------------
    # 子代理模型分级
    # ------------------------------------------------------------------

    def get_delegation_tiers(self) -> Dict[int, List[Dict[str, Any]]]:
        return self._manager().get_delegation_tiers()

    def set_delegation_tier(self, tier: int, model_ids: List[str]) -> bool:
        return self._manager().set_delegation_tier(tier, model_ids)

    # ------------------------------------------------------------------
    # 默认 / 热切换
    # ------------------------------------------------------------------

    def set_default(self, model_id: str) -> bool:
        """设置默认对话模型。chat 模型必须支持工具调用。"""
        mgr = self._manager()
        if not mgr.set_default(model_id):
            return False
        self._apply_llm_switch()
        return True

    def _apply_llm_switch(self) -> None:
        rt = get_runtime()
        if rt is None:
            return
        try:
            rt.switch_llm(self._manager().get_default())
        except Exception:
            from core.log import log
            log("LLM 热切换失败", "ERROR")

    # ------------------------------------------------------------------
    # 连接测试 / 能力探测
    # ------------------------------------------------------------------

    @staticmethod
    def _validate_remote_url(base_url: str, has_credentials: bool) -> None:
        """校验远程 URL：仅允许 http/https；携带凭据时禁止云元数据等高危目标。

        防止把已保存的 API Key 随请求发送到链接本地/元数据地址（凭据外泄）。
        私网/回环地址本身允许（Ollama 等本地部署场景），但会记 WARNING。
        """
        from urllib.parse import urlparse

        parsed = urlparse(base_url.strip())
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise ValueError(f"非法的 base_url: {base_url!r}（仅支持 http/https）")
        host = parsed.hostname.lower()
        if has_credentials:
            blocked = ("169.254.169.254", "metadata.google.internal", "100.100.100.200")
            if host in blocked or host.endswith(".internal"):
                raise ValueError(f"禁止携带凭据访问元数据地址: {host}")
            if host not in ("127.0.0.1", "localhost", "::1") and (
                host.startswith(("10.", "192.168.", "169.254."))
                or any(host.startswith(f"172.{i}.") for i in range(16, 32))
            ):
                from core.log import log
                log(f"携带凭据访问私网地址 {host}，请确认目标可信", "WARNING")

    async def test_connection(
        self, base_url: str, api_key: str, api_type: str = "openai",
        extra_headers: Optional[Dict[str, str]] = None,
    ) -> str:
        import httpx

        from agent.llm.url_utils import models_endpoint_candidates

        self._validate_remote_url(base_url, bool(api_key))
        headers = self._auth_headers(api_key, api_type, extra_headers)
        candidates = models_endpoint_candidates(base_url)
        if not candidates:
            raise ValueError(f"非法的 base_url: {base_url!r}")
        async with httpx.AsyncClient(timeout=10.0) as c:
            r = None
            for url in candidates:
                r = await c.get(url, headers=headers)
                if r.status_code != 404:
                    break
            if r is not None and r.status_code == 200:
                data = r.json()
                names = [m.get("id") or m.get("name", "") for m in self._extract_model_entries(data)][:8]
                names = [n for n in names if n]
                return f"连接成功! 可用模型: {', '.join(names)}" if names else "连接成功 (无模型列表)"
            return f"连接成功 (HTTP {r.status_code if r is not None else '无响应'})"

    # 各 api_type 的默认接口地址：统一引用 agent.llm.config.DEFAULT_BASE_URLS，
    # 消除多副本漂移（此前与 llm_client._LITELLM_PREFIX_MAP 已发生过漂移）
    @staticmethod
    def _default_base_urls() -> Dict[str, str]:
        from agent.llm.config import DEFAULT_BASE_URLS
        return DEFAULT_BASE_URLS

    @staticmethod
    def _auth_headers(
        api_key: str, api_type: str = "openai",
        extra_headers: Optional[Dict[str, str]] = None,
    ) -> Dict[str, str]:
        """按 api_type 构造鉴权头，自定义请求头最后合并（可覆盖任意头）。"""
        headers: Dict[str, str] = {}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        if api_type == "anthropic":
            # Anthropic 官方及中转站通常要求 x-api-key + 版本头（双头兼容）
            if api_key:
                headers["x-api-key"] = api_key
            headers["anthropic-version"] = "2023-06-01"
        if extra_headers:
            headers.update(extra_headers)
        return headers

    @staticmethod
    def _extract_model_entries(data: Any) -> List[Dict[str, Any]]:
        """从模型列表响应中提取模型条目。

        兼容 OpenAI 形 {"data": [...]} 与 {"models": [...]}（Gemini 等），
        条目 id 取 id 或 name 字段（参考 cursor-byok extractModelIDs）。
        """
        if not isinstance(data, dict):
            return []
        entries = data.get("data")
        if not isinstance(entries, list):
            entries = data.get("models")
        if not isinstance(entries, list):
            return []
        return [m for m in entries if isinstance(m, dict)]

    async def fetch_remote_models(
        self, base_url: str, api_key: str, api_type: str = "openai",
        proxy_url: str = "",
        extra_headers: Optional[Dict[str, str]] = None,
    ) -> List[Dict[str, Any]]:
        """从供应商 API 拉取远程可用模型列表，自动适配不同 api_type。

        端点候选回退（/models → /v1/models）；Anthropic 协议自动游标分页
        （limit=1000 + after_id，has_more/last_id 驱动，最多 50 页）。
        """
        import httpx

        from agent.llm.url_utils import models_endpoint_candidates

        effective_url = base_url.strip() or self._default_base_urls().get(api_type, "")
        if not effective_url:
            return []
        self._validate_remote_url(effective_url, bool(api_key))

        headers = self._auth_headers(api_key, api_type, extra_headers)

        proxy: Optional[str] = None
        if proxy_url:
            p = proxy_url.strip()
            if p and not p.startswith(("http://", "https://", "socks5://", "socks4://")):
                p = f"http://{p}"
            proxy = p

        candidates = models_endpoint_candidates(effective_url)
        async with httpx.AsyncClient(timeout=15.0, proxy=proxy) as c:
            r = None
            for url in candidates:
                r = await c.get(url, headers=headers)
                if r.status_code != 404:
                    break
            if r is None:
                return []
            r.raise_for_status()
            entries = self._extract_model_entries(r.json())

            # Anthropic 游标分页：首响应含 has_more/last_id 时继续翻页
            if api_type == "anthropic":
                data = r.json()
                pages = 1
                while (
                    isinstance(data, dict)
                    and data.get("has_more")
                    and data.get("last_id")
                    and pages < 50
                ):
                    r = await c.get(
                        r.url,
                        params={"limit": 1000, "after_id": data["last_id"]},
                        headers=headers,
                    )
                    r.raise_for_status()
                    data = r.json()
                    entries.extend(self._extract_model_entries(data))
                    pages += 1

            seen: set = set()
            result: List[Dict[str, Any]] = []
            for m in entries:
                model_id = m.get("id") or m.get("name") or ""
                if not model_id or model_id in seen:
                    continue
                seen.add(model_id)
                result.append({
                    "id": model_id,
                    "owned_by": m.get("owned_by", m.get("created_by", "")),
                    "created": m.get("created") or m.get("created_at"),
                })
            result.sort(key=lambda x: x["id"])
            return result

    async def fetch_provider_remote_models(
        self, provider_id: str,
    ) -> List[Dict[str, Any]]:
        """通过已配置的供应商凭据拉取远程可用模型列表。"""
        mgr = self._manager()
        prov = mgr.get_provider(provider_id)
        if prov is None:
            return []
        return await self.fetch_remote_models(
            prov.base_url, prov.api_key, prov.api_type, prov.proxy_url,
        )

    @staticmethod
    def get_model_info(model: str, api_type: str = "openai") -> Dict[str, Any]:
        """通过 litellm 查询模型的能力和参数上限。"""
        import litellm

        from agent.llm.config import _LITELLM_PREFIX_MAP

        prefix = _LITELLM_PREFIX_MAP.get(api_type, "openai")
        litellm_model = f"{prefix}/{model}"

        try:
            info = litellm.get_model_info(litellm_model)
            return {
                "max_output_tokens": info.get("max_output_tokens", 4096),
                "max_input_tokens": info.get("max_input_tokens", 0),
                "supports_vision": info.get("supports_vision", False),
                "supports_tools": info.get("supports_function_calling", True),
                "input_cost_per_token": info.get("input_cost_per_token"),
                "output_cost_per_token": info.get("output_cost_per_token"),
                "found": True,
            }
        except Exception:
            return {"found": False}

    def get_all_model_ids(self) -> List[str]:
        """返回所有已配置的模型 ID 列表。"""
        return self._manager().all_model_ids

    async def probe_capabilities(
        self, base_url: str, api_key: str, model: str, api_type: str = "openai",
        provider_id: str = "",
    ) -> Dict[str, Any]:
        from agent.llm.llm_client import LLMClient as _LC
        proxy_url = ""
        if provider_id:
            provider = self._manager().get_provider(provider_id)
            proxy_url = provider.proxy_url if provider is not None else ""
        return await _LC.probe_capabilities(
            base_url, api_key, model, api_type=api_type, proxy_url=proxy_url,
        )

    # 保存并测试的固定探针 prompt（参考 cursor-byok 的基准测试设计：
    # 测的就是生产流式链路，而非单独的 ping）
    _TEST_CHAT_PROMPT = "Output the numbers 1 through 50, separated by spaces."

    async def test_chat(
        self,
        provider_id: str,
        model_id: str = "",
        draft: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """真实链路对话测试：流式请求测量首字延迟/总耗时/输出 token。

        draft 为前端编辑中的模型草稿（模型级字段），与已保存配置合并后
        构造临时客户端——测的就是用户即将保存的那份配置，而非线上旧配置。
        连接参数（base_url/api_key/api_type/proxy_url）始终以供应商为准。
        """
        import time

        from agent.llm.config import LLMClientConfig
        from agent.llm.llm_client import LLMClient as _LC

        mgr = self._manager()
        prov = mgr.get_provider(provider_id)
        if prov is None:
            raise ValueError(f"供应商 '{provider_id}' 不存在")

        cfg_dict: Dict[str, Any] = {}
        existing = mgr.get_client(model_id) if model_id else None
        if existing is not None:
            cfg_dict = existing.config.to_dict()
            # to_dict 未覆盖的字段显式补齐，保证测试配置与线上一致
            cfg_dict["reasoning_effort"] = existing.config.reasoning_effort
            cfg_dict["embedding_dims"] = existing.config.embedding_dims
            cfg_dict["embedding_max_batch"] = existing.config.embedding_max_batch
        cfg_dict.update({
            "name": model_id or "__test__",
            "base_url": prov.base_url,
            "api_key": prov.api_key,
            "api_type": prov.api_type,
            "proxy_url": prov.proxy_url,
            "provider_id": provider_id,
        })
        if draft:
            cfg_dict.update({
                k: v for k, v in draft.items()
                if k in LLMClientConfig.__dataclass_fields__
                and k not in ("name", "base_url", "api_key", "api_type", "proxy_url")
            })
        if not str(cfg_dict.get("model") or "").strip():
            return {"ok": False, "error": "尚未填写模型标识"}

        client = _LC(LLMClientConfig(**cfg_dict))
        start = time.monotonic()
        ttft: Optional[float] = None
        text_parts: List[str] = []
        output_tokens = 0
        try:
            stream = client.chat_stream(
                [{"role": "user", "content": self._TEST_CHAT_PROMPT}],
                options={"max_tokens": 256},
            )
            async for delta in stream:
                if (delta.content or delta.reasoning_content) and ttft is None:
                    ttft = time.monotonic() - start
                if delta.content:
                    text_parts.append(delta.content)
                if delta.usage and delta.usage.completion_tokens:
                    output_tokens = delta.usage.completion_tokens
            total = time.monotonic() - start
            text = "".join(text_parts).strip()
            if not text:
                return {"ok": False, "error": "模型返回空结果"}
            # 端点未返回 usage 时按字符数估算并明确标注（参考 cursor-byok
            # estimateBenchmarkTextTokens 的 tokensEstimated 语义）
            tokens_estimated = output_tokens <= 0
            if tokens_estimated:
                output_tokens = max(1, round(len(text) / 4))
            return {
                "ok": True,
                "ttft_ms": round((ttft if ttft is not None else total) * 1000),
                "total_ms": round(total * 1000),
                "output_tokens": output_tokens,
                "tokens_estimated": tokens_estimated,
                "reply_preview": text[:120],
            }
        except Exception as e:
            return {"ok": False, "error": self.sanitize_error(e)}
        finally:
            await client.close()
