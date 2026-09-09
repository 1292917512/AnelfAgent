"""Dify 平台实体工具 — 连接外部 Dify 实例的桥梁：应用与工作流管理、运行调用、MCP 桥接。

定位：Dify 由外部环境承载（用户自部署 / 云端 / 内网），本实体负责连接与驱动；
尚未部署时 AI 可借助 devops/ssh 等既有能力自行部署后再连接。

整组为可沉睡分组：默认仅目录展示 brief，AI 调用 activate_tool_group 唤醒；
消息中出现 [dify:...] 标签时整组自动激活（tags=["dify"]）。

Model Experience:
- 模型看到：应用引用统一接受 app_id 或应用名（重名时报错并列候选）；
  错误 JSON 统一携带 cause/hint/retryable
- token 影响：列表类返回精简字段；DSL 导出为大字段，仅在编辑场景调用
- 缓存影响：仅 tool_chain 动态区，不触碰前缀缓存层

权限分级（框架审批引擎自动求值）：
- 只读（status/connect/list/export/run/chat）：默认放行
- 写操作（凭据/删除/导入/覆盖/发布/MCP 变更）：risk=CRITICAL
"""

from __future__ import annotations

import json
from typing import Any

from entities._sdk import (
    ErrorCause,
    entity,
    error_from_exception,
    tool,
    tool_error,
)

from . import service
from .client import DifyApiError, DifyAuthError, DifyNotFoundError
from .dsl import DslError
from .service import DifyStateError

entity("dify", "Dify 平台 - 连接既有 Dify 实例：应用与工作流 DSL 管理、运行调用、MCP 桥接")

_SLEEP_BRIEF = "Dify 平台（连接管理 / 应用与工作流 / 运行调用 / MCP 桥接）"
_TAGS = ["dify"]


def _result(action: str, **fields: Any) -> str:
    return json.dumps({"action": action, **fields}, ensure_ascii=False)


def _map_error(exc: Exception, action: str) -> str:
    """实体异常 → 归因明确的工具错误 JSON。"""
    if isinstance(exc, DifyNotFoundError):
        return tool_error(str(exc), cause=ErrorCause.NOT_FOUND, retryable=False)
    if isinstance(exc, DifyAuthError):
        return tool_error(str(exc), cause=ErrorCause.PERMISSION, retryable=False,
                          hint="用 dify_setup_admin 录入/更新管理员凭据，或检查 Dify 控制台账号")
    if isinstance(exc, DifyStateError):
        return tool_error(str(exc), cause=ErrorCause.STATE, retryable=False)
    if isinstance(exc, DslError):
        return tool_error(str(exc), cause=ErrorCause.PARAM, retryable=False,
                          hint="先用 dify_export_dsl 导出现有应用作为 DSL 模板再修改")
    if isinstance(exc, DifyApiError):
        cause = ErrorCause.NETWORK if exc.status == 0 else ErrorCause.INTERNAL
        return tool_error(str(exc), cause=cause, retryable=exc.status == 0)
    return error_from_exception(exc, action=action)


def _parse_json_object(raw: str, field: str) -> dict:
    """把 LLM 传入的 JSON 字符串解析为对象（容错空串）。"""
    text = (raw or "").strip()
    if not text:
        return {}
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise DslError(f"{field} 不是合法 JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise DslError(f"{field} 必须是 JSON 对象")
    return data


# ── 连接与状态 ────────────────────────────────────────────────────────


@tool(name="dify_status", group="dify", tags=_TAGS, concurrency_safe=True,
      allow_sleep=True, sleep_brief=_SLEEP_BRIEF, timeout=30)
async def dify_status() -> str:
    """查看 Dify 连接状态（地址 / 可达性 / 初始化进度 / 版本 / 凭据是否就绪）。

    进行任何 Dify 操作前建议先调用本工具确认连接状态。
    """
    try:
        return _result("dify_status", **(await service.get_status()))
    except Exception as exc:
        return _map_error(exc, "查询 Dify 状态")


@tool(name="dify_connect", group="dify", tags=_TAGS, concurrency_safe=True,
      allow_sleep=True, sleep_brief=_SLEEP_BRIEF, timeout=60)
async def dify_connect() -> str:
    """连接 Dify 实例：探测可达性，未初始化时自动创建管理员，已初始化则验证凭据。

    前置：实体配置 dify_base_url 指向已部署的 Dify（如 http://127.0.0.1:8899）。
    幂等可重调：凭据失效或更换实例后重新握手。Dify 尚未部署时，
    先用运维/SSH 能力完成部署（官方 docker compose），再回本工具连接。
    """
    try:
        return _result("dify_connect", **(await service.connect()))
    except Exception as exc:
        return _map_error(exc, "连接 Dify")


@tool(name="dify_setup_admin", group="dify", tags=_TAGS, risk="CRITICAL",
      allow_sleep=True, sleep_brief=_SLEEP_BRIEF, timeout=60)
async def dify_setup_admin(email: str, password: str) -> str:
    """录入既有 Dify 实例的管理员凭据（接管已完成初始化的实例时使用）。

    凭据会先经真实登录验证，通过后才保存；未初始化的实例请直接用 dify_connect
    自动创建管理员。

    Args:
        email: 管理员邮箱
        password: 管理员密码
    """
    try:
        return _result("dify_setup_admin", **(await service.setup_admin_manual(email, password)))
    except Exception as exc:
        return _map_error(exc, "录入管理员凭据")


@tool(name="dify_console_overview", group="dify", tags=_TAGS, concurrency_safe=True,
      allow_sleep=True, sleep_brief=_SLEEP_BRIEF, timeout=90)
async def dify_console_overview() -> str:
    """Dify 控制台概览（应用数 / 数据集数 / 应用清单摘要），用于了解平台现状。"""
    try:
        return _result("dify_console_overview", **(await service.console_overview()))
    except Exception as exc:
        return _map_error(exc, "获取控制台概览")


# ── 应用与工作流管理 ──────────────────────────────────────────────────


@tool(name="dify_list_apps", group="dify", tags=_TAGS, concurrency_safe=True,
      allow_sleep=True, sleep_brief=_SLEEP_BRIEF, timeout=90)
async def dify_list_apps(name_filter: str = "") -> str:
    """列出 Dify 应用（id / 名称 / 模式 / 是否有 API Key / MCP 状态）。

    Args:
        name_filter: 按名称过滤（可选）
    """
    try:
        return _result("dify_list_apps", **(await service.list_apps(name_filter)))
    except Exception as exc:
        return _map_error(exc, "列出应用")


@tool(name="dify_create_app", group="dify", tags=_TAGS, risk="CRITICAL",
      allow_sleep=True, sleep_brief=_SLEEP_BRIEF, timeout=120)
async def dify_create_app(name: str, mode: str, description: str = "") -> str:
    """创建空白应用（自动开通 API 并签发运行时 Key）。

    创建后通常流程：dify_export_dsl 查看结构 → 修改 DSL → dify_apply_dsl 覆盖 →
    dify_publish 发布（工作流类）→ dify_run_workflow / dify_chat 运行。

    Args:
        name: 应用名称
        mode: 应用模式（chat 对话 / agent-chat / advanced-chat 高级编排对话 /
              workflow 工作流 / completion 文本生成）
        description: 应用描述（可选）
    """
    try:
        return _result("dify_create_app", **(await service.create_app(name, mode, description)))
    except Exception as exc:
        return _map_error(exc, "创建应用")


@tool(name="dify_export_dsl", group="dify", tags=_TAGS, concurrency_safe=True,
      allow_sleep=True, sleep_brief=_SLEEP_BRIEF, timeout=90)
async def dify_export_dsl(app: str) -> str:
    """导出应用的完整 DSL（YAML），含工作流图/提示词/模型配置，是理解与修改应用的入口。

    Args:
        app: 应用引用（app_id 或应用名）
    """
    try:
        return _result("dify_export_dsl", **(await service.export_dsl(app)))
    except Exception as exc:
        return _map_error(exc, "导出应用 DSL")


@tool(name="dify_import_dsl", group="dify", tags=_TAGS, risk="CRITICAL",
      allow_sleep=True, sleep_brief=_SLEEP_BRIEF, timeout=120)
async def dify_import_dsl(yaml_content: str, new_name: str = "") -> str:
    """用 DSL（YAML）新建应用（自动开通 API 并签发 Key）。

    Args:
        yaml_content: 完整 DSL YAML 文本（可基于 dify_export_dsl 的产物修改）
        new_name: 覆盖 DSL 中的应用名（可选）
    """
    try:
        return _result("dify_import_dsl", **(await service.import_dsl(yaml_content, new_name)))
    except Exception as exc:
        return _map_error(exc, "导入 DSL")


@tool(name="dify_apply_dsl", group="dify", tags=_TAGS, risk="CRITICAL",
      allow_sleep=True, sleep_brief=_SLEEP_BRIEF, timeout=120)
async def dify_apply_dsl(app: str, yaml_content: str, publish: bool = False) -> str:
    """把修改后的 DSL 覆盖到既有应用（工作流类写入草稿，对话/补全类写入模型配置）。

    DSL 模式必须与目标应用一致；返回变更统计。工作流类应用覆盖后处于草稿态，
    需 publish=true 或另调 dify_publish 才会对线上生效。

    Args:
        app: 应用引用（app_id 或应用名）
        yaml_content: 完整 DSL YAML 文本（建议基于 dify_export_dsl 的产物修改，勿手写整个图）
        publish: 覆盖后立即发布（仅工作流类应用有效）
    """
    try:
        return _result("dify_apply_dsl", **(await service.apply_dsl(app, yaml_content, publish)))
    except Exception as exc:
        return _map_error(exc, "覆盖应用 DSL")


@tool(name="dify_publish", group="dify", tags=_TAGS, risk="CRITICAL",
      allow_sleep=True, sleep_brief=_SLEEP_BRIEF, timeout=90)
async def dify_publish(app: str, marked_name: str = "", marked_comment: str = "") -> str:
    """发布工作流类应用的当前草稿（发布后线上运行立即使用新版本）。

    Args:
        app: 应用引用（app_id 或应用名）
        marked_name: 版本标记名（可选，≤20 字符）
        marked_comment: 版本备注（可选，≤100 字符）
    """
    try:
        return _result("dify_publish", **(await service.publish(app, marked_name, marked_comment)))
    except Exception as exc:
        return _map_error(exc, "发布工作流")


@tool(name="dify_copy_app", group="dify", tags=_TAGS,
      allow_sleep=True, sleep_brief=_SLEEP_BRIEF, timeout=90)
async def dify_copy_app(app: str, new_name: str = "") -> str:
    """复制应用生成副本（副本需单独签发 API Key：dify_create_api_key）。

    Args:
        app: 应用引用（app_id 或应用名）
        new_name: 副本名称（可选）
    """
    try:
        return _result("dify_copy_app", **(await service.copy_app(app, new_name)))
    except Exception as exc:
        return _map_error(exc, "复制应用")


@tool(name="dify_delete_app", group="dify", tags=_TAGS, risk="CRITICAL",
      allow_sleep=True, sleep_brief=_SLEEP_BRIEF, timeout=90)
async def dify_delete_app(app: str) -> str:
    """删除应用（不可恢复；本地保存的 API Key 与 MCP 桥接一并清理）。

    Args:
        app: 应用引用（app_id 或应用名）
    """
    try:
        return _result("dify_delete_app", **(await service.delete_app(app)))
    except Exception as exc:
        return _map_error(exc, "删除应用")


# ── 运行调用 ──────────────────────────────────────────────────────────


@tool(name="dify_run_workflow", group="dify", tags=_TAGS,
      allow_sleep=True, sleep_brief=_SLEEP_BRIEF, timeout=300)
async def dify_run_workflow(app: str, inputs_json: str = "{}") -> str:
    """运行工作流类应用（同步等待结果，返回 outputs / 耗时 / token 用量）。

    应用须已发布（dify_publish）；inputs 的键名由应用的输入变量定义决定，
    可先用 dify_export_dsl 查看 workflow.graph 中 start 节点的 variables。

    Args:
        app: 应用引用（app_id 或应用名）
        inputs_json: 输入变量（JSON 对象字符串，如 {"query": "你好"}）
    """
    try:
        inputs = _parse_json_object(inputs_json, "inputs_json")
        return _result("dify_run_workflow", **(await service.run_workflow(app, inputs)))
    except Exception as exc:
        return _map_error(exc, "运行工作流")


@tool(name="dify_chat", group="dify", tags=_TAGS,
      allow_sleep=True, sleep_brief=_SLEEP_BRIEF, timeout=300)
async def dify_chat(app: str, query: str, inputs_json: str = "{}",
                    conversation_id: str = "") -> str:
    """向对话型应用（chat / agent-chat / advanced-chat）发送消息，返回回答。

    Args:
        app: 应用引用（app_id 或应用名）
        query: 用户消息内容
        inputs_json: 应用自定义输入变量（JSON 对象字符串，可空）
        conversation_id: 会话 ID（传入上次返回值可延续多轮对话，留空开新会话）
    """
    try:
        inputs = _parse_json_object(inputs_json, "inputs_json")
        return _result("dify_chat",
                       **(await service.chat(app, query, inputs, conversation_id)))
    except Exception as exc:
        return _map_error(exc, "发送对话消息")


# ── API Key 管理 ──────────────────────────────────────────────────────


@tool(name="dify_list_api_keys", group="dify", tags=_TAGS, concurrency_safe=True,
      allow_sleep=True, sleep_brief=_SLEEP_BRIEF, timeout=60)
async def dify_list_api_keys(app: str) -> str:
    """列出应用的 API Key（脱敏，仅显示前缀）。

    Args:
        app: 应用引用（app_id 或应用名）
    """
    try:
        return _result("dify_list_api_keys", **(await service.list_api_keys(app)))
    except Exception as exc:
        return _map_error(exc, "列出 API Key")


@tool(name="dify_create_api_key", group="dify", tags=_TAGS, risk="CRITICAL",
      allow_sleep=True, sleep_brief=_SLEEP_BRIEF, timeout=60)
async def dify_create_api_key(app: str) -> str:
    """为应用签发新的 API Key（自动保存为本地默认运行时 Key）。

    Args:
        app: 应用引用（app_id 或应用名）
    """
    try:
        return _result("dify_create_api_key", **(await service.create_api_key(app)))
    except Exception as exc:
        return _map_error(exc, "签发 API Key")


@tool(name="dify_revoke_api_key", group="dify", tags=_TAGS, risk="CRITICAL",
      allow_sleep=True, sleep_brief=_SLEEP_BRIEF, timeout=60)
async def dify_revoke_api_key(app: str, api_key_id: str) -> str:
    """吊销应用的指定 API Key（立即失效）。

    Args:
        app: 应用引用（app_id 或应用名）
        api_key_id: 要吊销的 Key ID（dify_list_api_keys 可查）
    """
    try:
        return _result("dify_revoke_api_key", **(await service.revoke_api_key(app, api_key_id)))
    except Exception as exc:
        return _map_error(exc, "吊销 API Key")


# ── 模型供应商与数据集 ────────────────────────────────────────────────


@tool(name="dify_list_model_providers", group="dify", tags=_TAGS, concurrency_safe=True,
      allow_sleep=True, sleep_brief=_SLEEP_BRIEF, timeout=90)
async def dify_list_model_providers() -> str:
    """列出 Dify 模型供应商及凭据配置状态（应用跑起来前通常需要先配好模型）。"""
    try:
        return _result("dify_list_model_providers", **(await service.list_model_providers()))
    except Exception as exc:
        return _map_error(exc, "列出模型供应商")


@tool(name="dify_set_model_credentials", group="dify", tags=_TAGS, risk="CRITICAL",
      allow_sleep=True, sleep_brief=_SLEEP_BRIEF, timeout=90)
async def dify_set_model_credentials(provider: str, credentials_json: str) -> str:
    """配置模型供应商凭据（如给 openai 写入 API Key，使 Dify 应用可调该家模型）。

    Args:
        provider: 供应商标识（如 openai / anthropic / deepseek / tongyi，
                  用 dify_list_model_providers 查看可用列表）
        credentials_json: 凭据 JSON 对象字符串（如 {"openai_api_key": "sk-..."}；
                  字段名由供应商定义，填错会返回 Dify 的校验错误信息）
    """
    try:
        credentials = _parse_json_object(credentials_json, "credentials_json")
        return _result("dify_set_model_credentials",
                       **(await service.set_model_credential(provider, credentials)))
    except Exception as exc:
        return _map_error(exc, "配置模型凭据")


@tool(name="dify_list_datasets", group="dify", tags=_TAGS, concurrency_safe=True,
      allow_sleep=True, sleep_brief=_SLEEP_BRIEF, timeout=60)
async def dify_list_datasets() -> str:
    """列出 Dify 知识库（数据集）。"""
    try:
        return _result("dify_list_datasets", **(await service.list_datasets()))
    except Exception as exc:
        return _map_error(exc, "列出数据集")


@tool(name="dify_create_dataset", group="dify", tags=_TAGS,
      allow_sleep=True, sleep_brief=_SLEEP_BRIEF, timeout=60)
async def dify_create_dataset(name: str, description: str = "") -> str:
    """创建知识库（文档上传与索引请在 Dify 控制台完成或后续扩展）。

    Args:
        name: 知识库名称
        description: 描述（可选）
    """
    try:
        return _result("dify_create_dataset", **(await service.create_dataset(name, description)))
    except Exception as exc:
        return _map_error(exc, "创建数据集")


# ── MCP 桥接 ──────────────────────────────────────────────────────────


@tool(name="dify_enable_mcp", group="dify", tags=_TAGS, risk="CRITICAL",
      allow_sleep=True, sleep_brief=_SLEEP_BRIEF, timeout=90)
async def dify_enable_mcp(app: str, description: str = "") -> str:
    """把应用暴露为 MCP Server 并桥接进 Anelf：应用能力即刻成为你可直接调用的 MCP 工具。

    桥接后该应用出现在 MCP 工具目录（组名 mcp:dify-<应用名>-xxxxxxxx），
    无需再走 dify_run_workflow / dify_chat 中转。应用须已发布。

    Args:
        app: 应用引用（app_id 或应用名）
        description: MCP Server 描述（可选，缺省用应用描述）
    """
    try:
        return _result("dify_enable_mcp", **(await service.enable_mcp(app, description)))
    except Exception as exc:
        return _map_error(exc, "启用 MCP 桥接")


@tool(name="dify_disable_mcp", group="dify", tags=_TAGS, risk="CRITICAL",
      allow_sleep=True, sleep_brief=_SLEEP_BRIEF, timeout=90)
async def dify_disable_mcp(app: str) -> str:
    """停用应用的 MCP Server 并移除 Anelf 侧桥接。

    Args:
        app: 应用引用（app_id 或应用名）
    """
    try:
        return _result("dify_disable_mcp", **(await service.disable_mcp(app)))
    except Exception as exc:
        return _map_error(exc, "停用 MCP 桥接")


@tool(name="dify_mcp_status", group="dify", tags=_TAGS, concurrency_safe=True,
      allow_sleep=True, sleep_brief=_SLEEP_BRIEF, timeout=60)
async def dify_mcp_status(app: str) -> str:
    """查询应用的 MCP Server 状态与端点 URL。

    Args:
        app: 应用引用（app_id 或应用名）
    """
    try:
        return _result("dify_mcp_status", **(await service.get_mcp_status(app)))
    except Exception as exc:
        return _map_error(exc, "查询 MCP 状态")
