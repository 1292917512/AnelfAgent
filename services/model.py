"""模型管理服务 -- 供应商/模型 CRUD、优先级管理、连接测试。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from services._runtime import get_runtime


class ModelService:

    @staticmethod
    def _manager() -> Any:
        from agent.core.llm import get_llm_manager
        return get_llm_manager()

    # ------------------------------------------------------------------
    # 供应商
    # ------------------------------------------------------------------

    def list_providers(self) -> List[Dict[str, Any]]:
        return self._manager().list_providers()

    def get_provider(self, pid: str) -> Optional[Dict[str, Any]]:
        prov = self._manager().get_provider(pid)
        return prov.to_dict() if prov else None

    def add_provider(self, pid: str, **kwargs: Any) -> bool:
        mgr = self._manager()
        if mgr.get_provider(pid):
            return False
        mgr.create_provider(pid, **kwargs)
        mgr.save_config()
        return True

    def update_provider(self, pid: str, **kwargs: Any) -> bool:
        return self._manager().update_provider(pid, **kwargs)

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
        d["api_key"] = cfg.api_key
        d["api_type"] = cfg.api_type
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

    async def test_connection(self, base_url: str, api_key: str) -> str:
        import httpx
        headers: Dict[str, str] = {}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        async with httpx.AsyncClient(timeout=10.0) as c:
            r = await c.get(f"{base_url.rstrip('/')}/models", headers=headers)
            if r.status_code == 200:
                data = r.json()
                names = [m.get("id", "") for m in data.get("data", [])][:8]
                return f"连接成功! 可用模型: {', '.join(names)}" if names else "连接成功 (无模型列表)"
            return f"连接成功 (HTTP {r.status_code})"

    async def probe_capabilities(
        self, base_url: str, api_key: str, model: str, api_type: str = "openai",
    ) -> Dict[str, Any]:
        from agent.core.llm.llm_client import LLMClient as _LC
        return await _LC.probe_capabilities(base_url, api_key, model, api_type=api_type)
