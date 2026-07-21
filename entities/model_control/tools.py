"""模型控制实体 — 列出/切换/参数调整/优先级控制 + Ollama 本地模型管理。

AI 通过这些工具可以自主完成：
- 查看所有可用模型及其能力
- 热切换当前思考模型（立即生效，同时持久化）
- 临时调整当前会话的模型参数（temperature、max_tokens）
- 查看/修改模型优先级顺序
- 管理 Ollama 本地模型（状态、拉取、删除、详情）— 仅在检测到本地安装 Ollama 时才注册
"""

from __future__ import annotations

import json
import shutil

from entities._sdk import tool, entity

entity("model_control", "模型控制 - 切换模型、调整参数、管理优先级")

# Ollama 工具仅在本地检测到 ollama 命令时才注册，避免无用工具占用资源
_OLLAMA_AVAILABLE = shutil.which("ollama") is not None
if _OLLAMA_AVAILABLE:
    entity("ollama", "本地 Ollama 模型管理 - 状态查询、模型拉取/删除/详情")


# ==================================================================
# 模型控制工具
# ==================================================================


@tool(name="list_models", group="model_control", tags=["core"],
      description="列出所有已配置的模型及其类型、能力和当前默认状态")
def list_models() -> str:
    """列出所有已配置的 LLM 模型，包含类型（chat/vision/embedding）、能力和当前默认标记。"""
    try:
        from agent.llm import get_llm_manager
        manager = get_llm_manager()

        summary = manager.get_models_summary()
        priorities = manager.get_type_priorities()
        default_name = manager.default_name

        result: dict = {
            "current_default": default_name,
            "model_summary": summary,
            "priorities": priorities,
        }
        return json.dumps(result, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


@tool(name="switch_model", group="model_control", tags=["core"],
      description="热切换当前使用的思考模型，立即生效并持久化配置")
def switch_model(model_name: str) -> str:
    """热切换当前使用的对话模型（立即对后续所有 LLM 调用生效，同时持久化配置）。

    Args:
        model_name: 要切换到的模型名称（通过 list_models 查看可用名称）
    """
    try:
        from services.model import ModelService
        svc = ModelService()
        ok = svc.set_default(model_name)
        if ok:
            return json.dumps({
                "ok": True,
                "message": f"已切换到模型 {model_name}，立即生效",
                "current_model": model_name,
            }, ensure_ascii=False)
        return json.dumps({
            "ok": False,
            "message": f"切换失败：模型 '{model_name}' 不存在或不支持工具调用，请用 list_models 查看可用列表",
        }, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


@tool(name="get_current_model", group="model_control", tags=["core"],
      description="查看当前使用的模型名称、配置参数和会话临时覆盖值")
def get_current_model() -> str:
    """查看当前使用的模型详情，包括名称、底层模型、温度、超时配置和会话临时参数。"""
    try:
        from services._runtime import require_runtime
        from agent.llm import get_llm_manager
        from agent.llm.llm_client import LLMClient

        rt = require_runtime()
        llm = rt.llm
        manager = get_llm_manager()

        info: dict = {
            "current_model_name": manager.default_name,
            "session_params": rt.mind._session_llm_params,
        }

        if isinstance(llm, LLMClient):
            cfg = llm.config
            info.update({
                "model": cfg.model,
                "temperature": cfg.temperature,
                "max_tokens": cfg.max_tokens if cfg.max_tokens else "auto（由模型默认决定）",
                "timeout": cfg.timeout,
                "supports_tools": cfg.supports_tools,
                "supports_vision": cfg.supports_vision,
            })

        return json.dumps(info, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


@tool(name="set_session_params", group="model_control", tags=["core"],
      description="临时调整当前会话的模型参数（temperature/max_tokens/reasoning_effort），不持久化，重启后恢复。传 -1 表示不修改该参数")
def set_session_params(temperature: float = -1.0, max_tokens: int = -1, reasoning_effort: str = "") -> str:
    """临时覆盖当前会话的模型参数，仅对本次运行有效，不写入配置文件。

    Args:
        temperature: 温度参数 0.0~2.0（传 -1 表示不修改）
        max_tokens: 最大输出 token 数（传 -1 表示不修改）
        reasoning_effort: 思考等级 low/medium/high/max（空字符串表示不修改，low 节省成本，high 深度思考）
    """
    try:
        from services._runtime import require_runtime
        rt = require_runtime()
        params = rt.mind._session_llm_params

        changed: list[str] = []
        if temperature >= 0:
            params["temperature"] = temperature
            changed.append(f"temperature={temperature}")
        if max_tokens > 0:
            params["max_tokens"] = max_tokens
            changed.append(f"max_tokens={max_tokens}")
        if reasoning_effort in ("low", "medium", "high", "max"):
            params["reasoning_effort"] = reasoning_effort
            changed.append(f"reasoning_effort={reasoning_effort}")

        if not changed:
            return json.dumps({
                "ok": True,
                "message": "未修改任何参数（传 -1 表示保持原值）",
                "current_session_params": params,
            }, ensure_ascii=False)

        return json.dumps({
            "ok": True,
            "changed": changed,
            "current_session_params": params,
            "note": "临时参数，仅本次运行有效，重启后恢复默认",
        }, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


@tool(name="clear_session_params", group="model_control", tags=["core"],
      description="清除本次会话的临时模型参数，恢复使用模型默认配置")
def clear_session_params() -> str:
    """清除所有临时会话参数，恢复使用模型默认的 temperature 和 max_tokens。"""
    try:
        from services._runtime import require_runtime
        rt = require_runtime()
        rt.mind._session_llm_params.clear()
        return json.dumps({"ok": True, "message": "已清除所有临时参数，恢复模型默认配置"}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


@tool(name="get_model_priority", group="model_control", tags=["core"],
      description="查看指定类型（chat/vision/embedding/rerank）的模型优先级顺序")
def get_model_priority(model_type: str = "chat") -> str:
    """查看指定模型类型的优先级列表（按优先级从高到低排列）。

    Args:
        model_type: 模型类型，支持 chat / vision / embedding / rerank，默认 chat
    """
    try:
        from agent.llm import get_llm_manager
        manager = get_llm_manager()
        priorities = manager.get_type_priorities()

        if model_type not in priorities:
            all_types = list(priorities.keys())
            return json.dumps({
                "error": f"未知模型类型 '{model_type}'，可用类型: {all_types}",
            }, ensure_ascii=False)

        return json.dumps({
            "model_type": model_type,
            "priority_order": priorities[model_type],
            "all_priorities": priorities,
        }, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


@tool(name="set_model_priority", group="model_control", tags=["core"],
      description="设置指定模型类型的优先级顺序（逗号分隔的模型 ID 列表），高优先级在前，持久化生效")
def set_model_priority(model_type: str, model_ids: str) -> str:
    """设置指定模型类型的优先级顺序，持久化到配置文件。

    Args:
        model_type: 模型类型，支持 chat / vision / embedding / rerank
        model_ids: 逗号分隔的模型 ID 列表，如 "gpt4o,claude3,qwen" （优先级从高到低）
    """
    try:
        from services.model import ModelService
        id_list = [s.strip() for s in model_ids.split(",") if s.strip()]
        if not id_list:
            return json.dumps({"error": "model_ids 不能为空"}, ensure_ascii=False)

        svc = ModelService()
        svc.set_type_priority(model_type, id_list)
        return json.dumps({
            "ok": True,
            "model_type": model_type,
            "new_priority": id_list,
            "message": "优先级已更新并持久化",
        }, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


# ==================================================================
# Ollama 本地模型管理工具（从 entities/ollama 迁移）
# ==================================================================


def _get_ollama():
    """获取 OllamaService 实例。"""
    import shutil
    import subprocess
    import json as _json
    from dataclasses import dataclass, field as dc_field
    from typing import Any, Dict, List, Optional
    from core.log import log

    _DEFAULT_HOST = "http://127.0.0.1:11434"

    @dataclass
    class _OllamaModelInfo:
        name: str
        size: str = ""
        modified: str = ""

    class _OllamaService:
        def __init__(self, host: str = _DEFAULT_HOST) -> None:
            self.host = host.rstrip("/")

        @staticmethod
        def is_installed() -> bool:
            return shutil.which("ollama") is not None

        def is_running(self) -> bool:
            try:
                import httpx
                r = httpx.get(f"{self.host}/api/version", timeout=3.0)
                return r.status_code == 200
            except Exception as e:
                log(f"Ollama 运行状态检测失败: {e}", "DEBUG")
                return False

        def get_version(self) -> str:
            try:
                result = subprocess.run(
                    ["ollama", "--version"], capture_output=True, text=True, timeout=5,
                )
                if result.returncode == 0:
                    return result.stdout.strip()
            except Exception as e:
                log(f"Ollama CLI 版本获取失败: {e}", "DEBUG")
            try:
                import httpx
                r = httpx.get(f"{self.host}/api/version", timeout=3.0)
                if r.status_code == 200:
                    return r.json().get("version", "unknown")
            except Exception as e:
                log(f"Ollama API 版本获取失败: {e}", "DEBUG")
            return ""

        def list_models(self) -> list:
            try:
                import httpx
                r = httpx.get(f"{self.host}/api/tags", timeout=5.0)
                if r.status_code == 200:
                    models = []
                    for m in r.json().get("models", []):
                        size_bytes = m.get("size", 0)
                        size_str = _fmt_size(size_bytes) if size_bytes else ""
                        models.append(_OllamaModelInfo(
                            name=m.get("name", ""),
                            size=size_str,
                            modified=m.get("modified_at", ""),
                        ))
                    return models
            except Exception as e:
                log(f"Ollama API 模型列表获取失败: {e}", "DEBUG")
            # CLI fallback
            try:
                result = subprocess.run(
                    ["ollama", "list"], capture_output=True, text=True, timeout=10,
                )
                if result.returncode == 0:
                    models = []
                    for line in result.stdout.strip().split("\n")[1:]:
                        parts = line.split()
                        if parts:
                            size = parts[2] + " " + parts[3] if len(parts) > 3 else ""
                            models.append(_OllamaModelInfo(
                                name=parts[0],
                                size=size,
                                modified=" ".join(parts[4:]) if len(parts) > 4 else "",
                            ))
                    return models
            except Exception as e:
                log(f"Ollama CLI 模型列表获取失败: {e}", "DEBUG")
            return []

        def pull_model(self, name: str):
            return subprocess.run(
                ["ollama", "pull", name], capture_output=True, text=True, timeout=600,
            )

        def delete_model(self, name: str):
            return subprocess.run(
                ["ollama", "rm", name], capture_output=True, text=True, timeout=30,
            )

        def show_model(self, name: str) -> dict:
            try:
                import httpx
                r = httpx.post(
                    f"{self.host}/api/show", json={"name": name}, timeout=5.0,
                )
                if r.status_code == 200:
                    return r.json()
            except Exception as e:
                log(f"Ollama 模型详情获取失败 ({name}): {e}", "DEBUG")
            return {}

        def get_status(self) -> dict:
            installed = self.is_installed()
            running = self.is_running() if installed else False
            version = self.get_version() if installed else ""
            models = self.list_models() if running else []
            return {
                "installed": installed,
                "running": running,
                "version": version,
                "model_count": len(models),
                "models": [m.name for m in models],
            }

    def _fmt_size(b: int) -> str:
        for unit in ("B", "KB", "MB", "GB"):
            if b < 1024:
                return f"{b:.1f} {unit}"
            b /= 1024
        return f"{b:.1f} TB"

    return _OllamaService()


if _OLLAMA_AVAILABLE:
    @tool(name="ollama_status", group="ollama",
          description="查询本地 Ollama 服务的运行状态、版本和可用模型列表")
    def ollama_status() -> str:
        """查询本地 Ollama 服务的运行状态、版本和可用模型列表。"""
        try:
            return json.dumps(_get_ollama().get_status(), ensure_ascii=False)
        except Exception as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)

    @tool(name="ollama_list_models", group="ollama",
          description="列出本地 Ollama 已有的所有模型")
    def ollama_list_models() -> str:
        """列出本地 Ollama 已有的所有模型，包含名称、大小和修改时间。"""
        try:
            models = _get_ollama().list_models()
            return json.dumps({
                "count": len(models),
                "models": [{"name": m.name, "size": m.size, "modified": m.modified} for m in models],
            }, ensure_ascii=False)
        except Exception as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)

    @tool(name="ollama_pull_model", group="ollama",
          description="拉取（下载）一个 Ollama 模型到本地，操作可能需要较长时间")
    def ollama_pull_model(model_name: str) -> str:
        """拉取（下载）一个 Ollama 模型到本地。

        Args:
            model_name: 要拉取的模型名称，如 llama3、gemma2、qwen2.5
        """
        try:
            result = _get_ollama().pull_model(model_name)
            if result.returncode == 0:
                return json.dumps({"ok": True, "message": f"模型 {model_name} 拉取成功"}, ensure_ascii=False)
            return json.dumps({"ok": False, "error": result.stderr.strip()}, ensure_ascii=False)
        except Exception as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)

    @tool(name="ollama_delete_model", group="ollama",
          description="删除本地已下载的 Ollama 模型")
    def ollama_delete_model(model_name: str) -> str:
        """删除本地已下载的 Ollama 模型。

        Args:
            model_name: 要删除的模型名称
        """
        try:
            result = _get_ollama().delete_model(model_name)
            if result.returncode == 0:
                return json.dumps({"ok": True, "message": f"模型 {model_name} 已删除"}, ensure_ascii=False)
            return json.dumps({"ok": False, "error": result.stderr.strip()}, ensure_ascii=False)
        except Exception as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)

    @tool(name="ollama_model_detail", group="ollama",
          description="查看本地 Ollama 模型的详细信息（参数量、量化级别、架构家族等）")
    def ollama_model_detail(model_name: str) -> str:
        """查看本地 Ollama 模型的详细信息。

        Args:
            model_name: 要查看的模型名称
        """
        try:
            detail = _get_ollama().show_model(model_name)
            if not detail:
                return json.dumps({"error": f"无法获取模型 {model_name} 的详情"}, ensure_ascii=False)
            d = detail.get("details", {})
            return json.dumps({
                "name": model_name,
                "format": d.get("format", ""),
                "parameter_size": d.get("parameter_size", ""),
                "quantization_level": d.get("quantization_level", ""),
                "family": d.get("family", ""),
                "license": (detail.get("license", "") or "")[:200],
            }, ensure_ascii=False)
        except Exception as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)
