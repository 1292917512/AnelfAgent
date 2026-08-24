"""模型控制实体 — 列出/切换/参数调整/优先级控制 + 子代理统一注册表 + Ollama 本地模型管理。

AI 通过这些工具可以自主完成：
- 查看所有可用模型及其能力（含运行时学习到的端点限制）
- 热切换当前思考模型（立即生效，同时持久化）
- 临时调整当前会话的模型参数（temperature、max_tokens）
- 持久化修复模型配置（update_model_config，固化端点行为问题的修复）
- 查看/修改模型优先级顺序
- 子代理统一注册表增删改查：内置难度档（easy/medium/hard，delegate_task 的
  difficulty 参数是其语法糖）与自定义档案同套 CRUD，候选池有序可含降级链
- 管理 Ollama 本地模型（状态、拉取、删除、详情）— 仅在检测到本地安装 Ollama 时才注册

Model Experience:
- 模型看到：本组工具 schema（静态注册）；档案内容不注入任何 prompt 层，
  AI 经 list_sub_agents 按需查询
- token 影响：工具 schema 增量（一次性），档案查询按需
- KV Cache 影响：无持续影响 — 无动态内容进入前缀层，档案变更不触碰任何缓存字节
"""

from __future__ import annotations

import json
import shutil
from typing import TYPE_CHECKING, Any

from entities._sdk import ErrorCause, entity, error_from_exception, tool, tool_error

if TYPE_CHECKING:
    from entities._sdk import LLMManager

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
        from entities._sdk import get_llm_manager
        manager = get_llm_manager()

        summary = manager.get_models_summary()
        priorities = manager.get_type_priorities()
        default_name = manager.default_name

        result: dict = {
            "current_default": default_name,
            "model_summary": summary,
            "priorities": priorities,
        }
        # 运行时学习到的端点限制（仅列出存在问题的模型）
        issues = _collect_runtime_issues(manager)
        if issues:
            result["runtime_issues"] = issues
            result["hint"] = "runtime_issues 为运行时临时自适应（重启失效），可用 update_model_config 固化修复"
        return json.dumps(result, ensure_ascii=False)
    except Exception as e:
        return error_from_exception(e, action="列出模型")


def _collect_runtime_issues(manager: "LLMManager") -> dict:
    """汇总各模型运行时学习到的端点限制（无问题返回空 dict）。"""
    issues: dict = {}
    for name in manager.get_all_names():
        client = manager.get_client(name)
        if client is None:
            continue
        found = client.get_runtime_issues()
        if found:
            issues[name] = found
    return issues


@tool(name="switch_model", group="model_control", tags=["core"],
      description="热切换当前使用的思考模型，立即生效并持久化配置")
def switch_model(model_name: str) -> str:
    """热切换当前使用的对话模型（立即对后续所有 LLM 调用生效，同时持久化配置）。

    Args:
        model_name: 要切换到的模型名称（通过 list_models 查看可用名称）
    """
    try:
        from entities._sdk import set_default_model
        ok = set_default_model(model_name)
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
        return error_from_exception(e, action=f"切换模型 '{model_name}'")


@tool(name="get_current_model", group="model_control", tags=["core"],
      description="查看当前使用的模型名称、配置参数和会话临时覆盖值")
def get_current_model() -> str:
    """查看当前使用的模型详情，包括名称、底层模型、温度、超时配置和会话临时参数。"""
    try:
        from entities._sdk import (
            get_active_llm_client,
            get_llm_client_class,
            get_llm_manager,
            get_session_llm_params,
        )

        llm = get_active_llm_client()
        manager = get_llm_manager()

        info: dict = {
            "current_model_name": manager.default_name,
            # 会话级临时参数覆盖：由 set_session_params/clear_session_params 写入、
            # mind 构建 LLM 选项时读取
            "session_params": get_session_llm_params(),
        }

        if isinstance(llm, get_llm_client_class()):
            cfg = llm.config
            info.update({
                "model": cfg.model,
                "temperature": cfg.temperature if cfg.temperature is not None else "auto（由模型默认决定）",
                "max_tokens": cfg.max_tokens if cfg.max_tokens else "auto（由模型默认决定）",
                "timeout": cfg.timeout,
                "supports_tools": cfg.supports_tools,
                "supports_vision": cfg.supports_vision,
            })
            issues = llm.get_runtime_issues()
            if issues:
                info["runtime_issues"] = issues
                info["hint"] = "runtime_issues 为运行时临时自适应（重启失效），可用 update_model_config 固化修复"

        return json.dumps(info, ensure_ascii=False)
    except Exception as e:
        return error_from_exception(e, action="获取当前模型信息")


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
        from entities._sdk import get_session_llm_params
        params = get_session_llm_params()

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
        return error_from_exception(e, action="设置会话参数")


@tool(name="clear_session_params", group="model_control", tags=["core"],
      description="清除本次会话的临时模型参数，恢复使用模型默认配置")
def clear_session_params() -> str:
    """清除所有临时会话参数，恢复使用模型默认的 temperature 和 max_tokens。"""
    try:
        from entities._sdk import get_session_llm_params
        get_session_llm_params().clear()
        return json.dumps({"ok": True, "message": "已清除所有临时参数，恢复模型默认配置"}, ensure_ascii=False)
    except Exception as e:
        return error_from_exception(e, action="清除会话参数")


# AI 可持久化修改的模型配置字段（保守白名单：仅端点行为与能力声明类参数，
# 连接/密钥字段 api_key/base_url/model 等不开放）
_UPDATABLE_FIELDS = {
    "timeout": float,
    "max_tokens": int,
    "context_window": int,
    "supports_forced_tool_choice": bool,
    "supports_reasoning": bool,
    "supports_vision": bool,
    "supports_tools": bool,
    "reasoning_effort": str,
}


@tool(name="update_model_config", group="model_control", tags=["core"],
      description="持久化修改指定模型的配置参数"
                  "（timeout/max_tokens/context_window/supports_forced_tool_choice/supports_reasoning"
                  "/supports_vision/supports_tools/reasoning_effort），"
                  "用于固化端点行为与能力声明（如 list_models 中 runtime_issues 提示的问题），重启后仍生效")
def update_model_config(model_name: str, field: str, value: str) -> str:
    """持久化修改模型配置并写入配置文件，立即生效。

    Args:
        model_name: 模型名称（通过 list_models 查看）
        field: 配置字段，可选值: timeout（秒）、max_tokens、context_window、
            supports_forced_tool_choice / supports_reasoning / supports_vision / supports_tools（布尔）、
            reasoning_effort（low/medium/high 或空串清除）
        value: 新值（按字段类型解析）
    """
    try:
        from entities._sdk import get_llm_manager
        manager = get_llm_manager()

        client = manager.get_client(model_name)
        if client is None:
            return json.dumps({
                "ok": False,
                "error": f"模型 '{model_name}' 不存在，请用 list_models 查看可用名称",
            }, ensure_ascii=False)

        if field not in _UPDATABLE_FIELDS:
            return json.dumps({
                "ok": False,
                "error": f"字段 '{field}' 不允许修改，可修改字段: {list(_UPDATABLE_FIELDS)}",
            }, ensure_ascii=False)

        parsed, parse_err = _parse_field_value(field, value)
        if parse_err:
            return json.dumps({"ok": False, "error": parse_err}, ensure_ascii=False)

        old_value = getattr(client.config, field)
        if not manager.update_model(model_name, **{field: parsed}):
            return tool_error(f"更新模型 '{model_name}' 失败，配置写入未生效",
                              cause=ErrorCause.INTERNAL, retryable=False, ok=False)

        return json.dumps({
            "ok": True,
            "message": f"已持久化 {model_name}.{field}: {old_value} -> {parsed}，立即生效且重启后保留",
            "model": model_name,
            "field": field,
            "old": old_value,
            "new": parsed,
        }, ensure_ascii=False)
    except Exception as e:
        return error_from_exception(e, action=f"更新模型 '{model_name}' 配置")


def _parse_field_value(field: str, value: str) -> tuple[Any, str]:
    """按字段类型解析配置值，返回 (解析值, 错误描述)。"""
    ptype = _UPDATABLE_FIELDS[field]
    text = value.strip()
    if ptype is bool:
        lowered = text.lower()
        if lowered in ("true", "1", "yes"):
            return True, ""
        if lowered in ("false", "0", "no"):
            return False, ""
        return None, f"字段 {field} 需要布尔值（true/false），收到: {value!r}"
    if ptype is str:
        from entities._sdk import canonical_efforts
        efforts = canonical_efforts()
        lowered = text.lower()
        if lowered in efforts or not lowered:
            return lowered, ""
        return None, f"字段 {field} 可选值: {sorted(efforts)} 或空串清除，收到: {value!r}"
    try:
        parsed = ptype(text)
    except (TypeError, ValueError):
        return None, f"字段 {field} 需要 {ptype.__name__} 类型，收到: {value!r}"
    if parsed <= 0:
        return None, f"字段 {field} 必须为正数，收到: {parsed}"
    return parsed, ""


@tool(name="get_model_priority", group="model_control", tags=["core"],
      description="查看指定类型（chat/vision/embedding/rerank）的模型优先级顺序")
def get_model_priority(model_type: str = "chat") -> str:
    """查看指定模型类型的优先级列表（按优先级从高到低排列）。

    Args:
        model_type: 模型类型，支持 chat / vision / embedding / rerank，默认 chat
    """
    try:
        from entities._sdk import get_llm_manager
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
        return error_from_exception(e, action="获取模型优先级")


@tool(name="set_model_priority", group="model_control", tags=["core"],
      description="设置指定模型类型的优先级顺序（逗号分隔的模型 ID 列表），高优先级在前，持久化生效")
def set_model_priority(model_type: str, model_ids: str) -> str:
    """设置指定模型类型的优先级顺序，持久化到配置文件。

    Args:
        model_type: 模型类型，支持 chat / vision / embedding / rerank
        model_ids: 逗号分隔的模型 ID 列表，如 "gpt4o,claude3,qwen" （优先级从高到低）
    """
    try:
        from entities._sdk import get_llm_manager
        id_list = [s.strip() for s in model_ids.split(",") if s.strip()]
        if not id_list:
            return json.dumps({"error": "model_ids 不能为空"}, ensure_ascii=False)

        get_llm_manager().set_type_priority(model_type, id_list)
        return json.dumps({
            "ok": True,
            "model_type": model_type,
            "new_priority": id_list,
            "message": "优先级已更新并持久化",
        }, ensure_ascii=False)
    except Exception as e:
        return error_from_exception(e, action="设置模型优先级")


# ==================================================================
# 子代理统一注册表（内置难度档 + 自定义档案，与 Web 管理面同路径，热更新生效）
# ==================================================================


@tool(name="list_sub_agents", group="model_control", tags=["core"],
      description="查看全部子代理档案（名称 → 有序模型候选池 + 描述）。"
                  "delegate_task 传 agent_name 即可使用对应档案；内置 easy/medium/hard "
                  "即难度三挡（difficulty 1/2/3 的语法糖），可直接指定或调整其模型池")
def list_sub_agents() -> str:
    """列出全部子代理档案，供 delegate_task 的 agent_name 参数选用。"""
    try:
        from entities._sdk import get_llm_manager
        profiles = get_llm_manager().list_sub_agents()
        return json.dumps({
            "sub_agents": profiles,
            "usage": "delegate_task(agent_name=档案名) 使用；difficulty 1/2/3 等价于 "
                     "agent_name=easy/medium/hard；内置档可用 update_sub_agent 调整模型池",
            "count": len(profiles),
        }, ensure_ascii=False)
    except Exception as e:
        return error_from_exception(e, action="查看子代理档案")


@tool(name="create_sub_agent", group="model_control", tags=["core"],
      description="创建自定义子代理档案（名称 + 绑定模型 + 描述），持久化并热生效。"
                  "创建后 delegate_task(agent_name=名称) 即使用该档案")
def create_sub_agent(name: str, model_id: str, description: str = "") -> str:
    """创建自定义子代理档案并持久化，立即生效。

    Args:
        name: 档案名（英文字母开头，仅字母/数字/下划线/连字符，≤32 字符，如 researcher；
            easy/medium/hard 为内置难度档保留名，不可创建）
        model_id: 绑定的模型 ID（须为已配置的 chat 模型，通过 list_models 查看）
        description: 用途描述（可选，帮助后续选用）
    """
    try:
        from entities._sdk import get_llm_manager
        ok, message = get_llm_manager().create_sub_agent(name, model_id, description)
        if not ok:
            return tool_error(message, cause=ErrorCause.PARAM, retryable=False)
        return json.dumps({"ok": True, "message": message, "name": name, "model_id": model_id},
                          ensure_ascii=False)
    except Exception as e:
        return error_from_exception(e, action=f"创建子代理档案 '{name}'")


@tool(name="update_sub_agent", group="model_control", tags=["core"],
      description="更新子代理档案（换绑模型/调整候选池/改描述），空参数保持原值，持久化并热生效。"
                  "内置难度档（easy/medium/hard）可经 models 调整其完整模型池（池内顺序即优先级，"
                  "前面的不可用时依次回退）")
def update_sub_agent(
        name: str,
        model_id: str = "",
        models: str = "",
        description: str = "",
) -> str:
    """更新子代理档案并持久化，立即生效。

    Args:
        name: 档案名（通过 list_sub_agents 查看，含内置 easy/medium/hard）
        model_id: 单模型快捷写法（与 models 互斥，后者优先；空 = 不变）
        models: 逗号分隔的完整候选池（如 "glm-flash,qwen-max"），整体替换、
            池内顺序即优先级；空 = 不变（清空池请经 Web 界面）
        description: 新描述（空 = 不变）
    """
    try:
        from entities._sdk import get_llm_manager
        pool = [s.strip() for s in models.split(",") if s.strip()] if models else None
        ok, message = get_llm_manager().update_sub_agent(
            name, model_id=model_id, models=pool, description=description,
        )
        if not ok:
            return tool_error(message, cause=ErrorCause.PARAM, retryable=False)
        return json.dumps({"ok": True, "message": message, "name": name}, ensure_ascii=False)
    except Exception as e:
        return error_from_exception(e, action=f"更新子代理档案 '{name}'")


@tool(name="delete_sub_agent", group="model_control", tags=["core"],
      description="删除自定义子代理档案，持久化并热生效（内置难度档 easy/medium/hard 受保护不可删除）")
def delete_sub_agent(name: str) -> str:
    """删除自定义子代理档案并持久化。

    Args:
        name: 档案名（通过 list_sub_agents 查看）
    """
    try:
        from entities._sdk import get_llm_manager
        ok, message = get_llm_manager().remove_sub_agent(name)
        if not ok:
            return tool_error(message, cause=ErrorCause.PARAM, retryable=False)
        return json.dumps({"ok": True, "message": message}, ensure_ascii=False)
    except Exception as e:
        return error_from_exception(e, action=f"删除子代理档案 '{name}'")


# ==================================================================
# Ollama 本地模型管理工具（从 entities/ollama 迁移）
# ==================================================================


def _get_ollama():
    """获取 OllamaService 实例。"""
    import shutil
    import subprocess
    from dataclasses import dataclass

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
            return error_from_exception(e, action="查询 Ollama 状态")

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
            return error_from_exception(e, action="列出 Ollama 模型")

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
            return error_from_exception(e, action=f"拉取模型 '{model_name}'")

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
            return error_from_exception(e, action=f"删除模型 '{model_name}'")

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
                return tool_error(f"无法获取模型 '{model_name}' 的详情",
                                  cause=ErrorCause.NOT_FOUND, retryable=False,
                                  hint="确认模型名称（ollama_list_models 查看）及 Ollama 服务是否运行")
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
            return error_from_exception(e, action=f"获取模型 '{model_name}' 详情")
