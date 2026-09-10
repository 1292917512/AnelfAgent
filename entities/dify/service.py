"""Dify 实体业务编排层 — 连接既有 Dify 实例的桥梁（外部部署优先）。

定位：Dify 由外部环境承载（用户自行 docker 部署 / 云端 / 内网实例；需要自动部署时
AI 可借助 devops/ssh 等既有能力自行完成），本实体负责"连接 → 接管 → 驱动"：
- 连接：base_url + 管理员凭据（自动初始化未初始化的自托管实例，或验证既有凭据）
- 管理：应用 CRUD / DSL 导出导入与覆盖 / 发布 / API Key 托管 / 模型供应商 / 数据集
- 运行：workflow 运行 / chat 对话（Service API，Key 自动签发托管）
- 桥接：Dify App → MCP Server → 注册进 Anelf MCP（热加载，AI 直接调用）

AI 工具（tools.py）与 HTTP 路由（router.py）共用本模块，杜绝平行实现。
client.py 承载全部 HTTP 细节，config.py 承载密钥存储（secrets.json）。
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Dict, List, Optional

from core.config import get_config, get_config_bool
from core.log import log

from .client import (
    DifyApiError,
    DifyAuthError,
    DifyConsoleClient,
    DifyNotFoundError,
    DifyServiceClient,
)
from .config import DifySecretsStore, generate_password, get_dify_store
from .dsl import (
    dsl_diff_stat,
    parse_dsl,
    rename_dsl_app,
    summarize_dsl,
    validate_dsl,
)

# 工作流类应用模式（走 draft/publish 链路）
_WORKFLOW_MODES = {"workflow", "advanced-chat"}
# 全部合法应用模式
_APP_MODES = ("chat", "agent-chat", "advanced-chat", "workflow", "completion")


class DifyStateError(Exception):
    """前置状态不满足（未连接/凭据缺失等），message 可直接展示。"""


# ------------------------------------------------------------------
# 配置读取
# ------------------------------------------------------------------


def get_settings() -> Dict[str, Any]:
    """当前实体配置快照（来自实体配置中心，面板/工具共用）。"""
    return {
        "enabled": get_config_bool("dify_enabled", True),
        "base_url": str(get_config("dify_base_url", "") or "").rstrip("/"),
        "admin_email": str(get_config("dify_admin_email", "admin@dify.local") or ""),
        "auto_setup": get_config_bool("dify_auto_setup", True),
        "context_inject": get_config_bool("dify_context_inject", True),
        "timeout": int(get_config("dify_timeout", 30) or 30),
    }


def _require_enabled() -> None:
    if not get_config_bool("dify_enabled", True):
        raise DifyStateError("Dify 实体已在配置中禁用（dify_enabled=false）")


def _require_base_url() -> str:
    base_url = get_settings()["base_url"]
    if not base_url:
        raise DifyStateError(
            "Dify 地址未配置：请在实体配置（或面板「连接」卡片）填写 dify_base_url，"
            "指向已部署的 Dify 实例（如 http://127.0.0.1:8899）；"
            "尚未部署时可借助运维/SSH 能力先完成部署"
        )
    return base_url


# ------------------------------------------------------------------
# 连接与状态
# ------------------------------------------------------------------


async def get_status() -> Dict[str, Any]:
    """连接状态：配置 / 可达性 / 初始化进度 / 版本 / 凭据（只读，不抛异常）。"""
    settings = get_settings()
    store = get_dify_store()
    base_url = settings["base_url"]

    setup: Optional[Dict[str, Any]] = None
    version = ""
    if base_url:
        async with DifyConsoleClient(base_url, timeout=8.0) as probe_client:
            setup = await probe_client.probe()
            if setup is not None:
                version = await probe_client.fetch_version()

    admin = store.get_admin()
    return {
        "enabled": settings["enabled"],
        "configured": bool(base_url),
        "base_url": base_url,
        "reachable": setup is not None,
        "setup_step": (setup or {}).get("step", ""),
        "version": version,
        "admin_configured": bool(admin.get("email") and admin.get("password")),
        "admin_email": admin.get("email", ""),
        "apps_tracked": len(store.list_apps()),
    }


async def connect() -> Dict[str, Any]:
    """连接 Dify 实例：探测可达性 → 未初始化且开启 auto_setup 则自动建管理员 → 验证凭据登录。

    幂等，可随时重调（凭据失效/实例更换后重新握手）。
    """
    _require_enabled()
    base_url = _require_base_url()
    settings = get_settings()
    store = get_dify_store()

    async with DifyConsoleClient(base_url, timeout=float(settings["timeout"])) as client:
        setup = await client.probe()
        if setup is None:
            raise DifyStateError(
                f"无法连接 {base_url}：请确认 Dify 已启动且地址正确（含端口，nginx 入口）"
            )
        step = str(setup.get("step") or "")
        version = await client.fetch_version()

        if step == "not_started":
            if not settings["auto_setup"]:
                return {
                    "ok": True, "reachable": True, "setup_step": step, "version": version,
                    "message": "Dify 可达但尚未初始化（auto_setup 已关闭）："
                               "请用 dify_setup_admin 录入凭据，或先在 Dify 控制台完成初始化",
                }
            email = settings["admin_email"] or "admin@dify.local"
            password = generate_password(20)
            await client.setup_admin(email=email, name="Anelf Admin", password=password)
            store.set_admin(email, password, created_by="auto")
            return {
                "ok": True, "reachable": True, "setup_step": "finished",
                "version": version, "auto_created": True, "email": email,
                "message": "管理员已自动创建并保存凭据（面板可查看，可登录 Dify 原生控制台）",
            }

        # 已初始化：验证本地凭据可登录
        admin = store.get_admin()
        if admin.get("email") and admin.get("password"):
            try:
                await client.login(str(admin["email"]), str(admin["password"]))
                return {
                    "ok": True, "reachable": True, "setup_step": step,
                    "version": version, "auto_created": False, "email": admin["email"],
                    "message": "连接成功，管理员凭据验证通过",
                }
            except DifyAuthError as exc:
                raise DifyStateError(
                    "Dify 已初始化但保存的凭据登录失败：请用 dify_setup_admin 更新凭据"
                ) from exc
        return {
            "ok": True, "reachable": True, "setup_step": step, "version": version,
            "message": "Dify 已初始化且本地无凭据：请用 dify_setup_admin 录入管理员凭据",
        }


async def setup_admin_manual(email: str, password: str) -> Dict[str, Any]:
    """录入既有 Dify 实例的管理员凭据（登录验证通过后才保存）。"""
    _require_enabled()
    base_url = _require_base_url()
    email = email.strip()
    if not email or not password:
        raise DifyStateError("邮箱与密码均不能为空")
    async with DifyConsoleClient(base_url) as client:
        status = await client.setup_status()
        await client.login(email, password)
    get_dify_store().set_admin(email, password, created_by="manual")
    return {
        "ok": True,
        "email": email,
        "setup_step": status.get("step", ""),
        "message": "管理员凭据验证通过并已保存",
    }


# ------------------------------------------------------------------
# Console 客户端工厂
# ------------------------------------------------------------------


@asynccontextmanager
async def console_client() -> AsyncIterator[DifyConsoleClient]:
    """创建带管理员凭据的 Console 客户端（用完自动关闭）。"""
    _require_enabled()
    base_url = _require_base_url()
    settings = get_settings()
    store = get_dify_store()
    admin = store.get_admin()
    client = DifyConsoleClient(base_url, timeout=float(settings["timeout"]))
    try:
        if admin.get("email") and admin.get("password"):
            client.set_credentials(str(admin["email"]), str(admin["password"]))
        yield client
    finally:
        await client.aclose()


async def _ensure_admin_session(client: DifyConsoleClient) -> None:
    """确保客户端已建立管理员会话；凭据缺失时给出可执行指引。"""
    try:
        await client.ensure_session()
    except DifyAuthError as exc:
        raise DifyStateError(
            f"Dify 管理员会话不可用：{exc}。"
            "请先调用 dify_connect 完成连接（含自动初始化），"
            "或用 dify_setup_admin 录入凭据"
        ) from exc


# ------------------------------------------------------------------
# 应用引用解析
# ------------------------------------------------------------------


async def _fetch_apps(client: DifyConsoleClient, name_filter: str = "") -> List[Dict[str, Any]]:
    """分页拉取全部应用（Dify 默认每页上限 100）。"""
    apps: List[Dict[str, Any]] = []
    page = 1
    while True:
        data = await client.list_apps(page=page, limit=100, name=name_filter)
        items = data.get("data") if isinstance(data, dict) else None
        if not isinstance(items, list):
            break
        apps.extend(a for a in items if isinstance(a, dict))
        if not data.get("has_more"):
            break
        page += 1
        if page > 50:
            break
    return apps


async def _resolve_app(client: DifyConsoleClient, ref: str) -> Dict[str, Any]:
    """按 app_id 或应用名解析应用（重名时报错并列出候选）。"""
    ref = ref.strip()
    if not ref:
        raise DifyStateError("应用引用不能为空（传 app_id 或应用名）")
    apps = await _fetch_apps(client)
    for app in apps:
        if str(app.get("id")) == ref:
            return app
    matches = [a for a in apps if str(a.get("name") or "") == ref]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        candidates = [f"{a.get('name')}({a.get('id')})" for a in matches[:5]]
        raise DifyStateError(f"应用名 '{ref}' 有 {len(matches)} 个重名，请改用 app_id: {candidates}")
    raise DifyNotFoundError(f"未找到应用: {ref}（可用 dify_list_apps 查看现有应用）", status=404)


# ------------------------------------------------------------------
# 应用与工作流管理
# ------------------------------------------------------------------


async def list_apps(name_filter: str = "") -> Dict[str, Any]:
    """列出应用（合并本地保存的 API Key / MCP 状态，Key 脱敏）。"""
    async with console_client() as client:
        await _ensure_admin_session(client)
        apps = await _fetch_apps(client, name_filter=name_filter)
    store = get_dify_store()
    tracked = store.list_apps()
    result = []
    for app in apps:
        app_id = str(app.get("id") or "")
        local = DifySecretsStore.mask_app(tracked.get(app_id) or {})
        result.append({
            "id": app_id,
            "name": app.get("name", ""),
            "mode": app.get("mode", ""),
            "description": app.get("description", ""),
            "enable_api": app.get("enable_api"),
            "has_api_key": bool(local.get("api_key")),
            "mcp_server_code": local.get("mcp_server_code", ""),
            "updated_at": app.get("updated_at"),
        })
    return {"ok": True, "count": len(result), "apps": result}


async def create_app(name: str, mode: str, description: str = "") -> Dict[str, Any]:
    """创建应用并自动开通 API + 签发运行时 Key（保存到本地密钥库）。"""
    name = name.strip()
    mode = mode.strip()
    if not name:
        raise DifyStateError("应用名不能为空")
    if mode not in _APP_MODES:
        raise DifyStateError(f"应用模式须为: {', '.join(_APP_MODES)}")
    async with console_client() as client:
        await _ensure_admin_session(client)
        created = await client.create_app(name, mode, description)
        app_id = str(created.get("id") or "")
        if not app_id:
            raise DifyApiError("创建应用响应缺少 id")
        api_key = await _ensure_api_key(client, app_id)
    get_dify_store().update_app(app_id, name=name, api_key=api_key)
    return {
        "ok": True,
        "app_id": app_id,
        "name": name,
        "mode": mode,
        "api_key_saved": bool(api_key),
        "message": "应用已创建，API 已开通，运行时 Key 已保存；"
                   "可用 dify_export_dsl 查看结构、dify_apply_dsl 覆盖内容后 dify_publish 发布",
    }


async def _ensure_api_key(client: DifyConsoleClient, app_id: str) -> str:
    """确保应用有可用 API Key：优先复用本地已存；其次复用远端既有；最后新建。"""
    store = get_dify_store()
    existing = store.get_api_key(app_id)
    if existing:
        return existing
    try:
        await client.set_api_enabled(app_id, True)
    except DifyApiError:
        pass  # 部分模式默认已开通，忽略
    keys = await client.list_api_keys(app_id)
    items = keys.get("data") if isinstance(keys, dict) else None
    if isinstance(items, list) and items:
        token = str(items[0].get("token") or "")
        if token:
            return token
    created = await client.create_api_key(app_id)
    token = str(created.get("token") or "")
    if not token:
        raise DifyApiError("创建 API Key 响应缺少 token")
    return token


async def delete_app(ref: str) -> Dict[str, Any]:
    """删除应用（同时清理本地保存的凭据与 MCP 桥接）。"""
    async with console_client() as client:
        await _ensure_admin_session(client)
        app = await _resolve_app(client, ref)
        app_id = str(app["id"])
        await client.delete_app(app_id)
    store = get_dify_store()
    local = store.get_app(app_id)
    if local.get("mcp_server_code"):
        await _unbridge_mcp(app_id)
    store.remove_app(app_id)
    return {"ok": True, "app_id": app_id, "name": app.get("name", ""),
            "message": "应用已删除（本地凭据与 MCP 桥接已一并清理）"}


async def copy_app(ref: str, new_name: str = "") -> Dict[str, Any]:
    async with console_client() as client:
        await _ensure_admin_session(client)
        app = await _resolve_app(client, ref)
        copied = await client.copy_app(str(app["id"]), name=new_name.strip())
    return {"ok": True, "app_id": str(copied.get("id") or ""),
            "name": copied.get("name", ""), "message": "应用已复制（副本需单独签发 API Key）"}


async def export_dsl(ref: str) -> Dict[str, Any]:
    """导出应用 DSL（YAML），附结构摘要。"""
    async with console_client() as client:
        await _ensure_admin_session(client)
        app = await _resolve_app(client, ref)
        yaml_text = await client.export_dsl(str(app["id"]))
    return {
        "ok": True,
        "app_id": str(app["id"]),
        "name": app.get("name", ""),
        "mode": app.get("mode", ""),
        "summary": summarize_dsl(yaml_text),
        "dsl": yaml_text,
    }


async def import_dsl(yaml_content: str, new_name: str = "") -> Dict[str, Any]:
    """以 DSL 新建应用（可选改名），自动开通 API + 签发 Key。"""
    summary = validate_dsl(yaml_content)
    if new_name.strip():
        yaml_content = rename_dsl_app(yaml_content, new_name)
        summary["app_name"] = new_name.strip()
    async with console_client() as client:
        await _ensure_admin_session(client)
        result = await client.import_dsl(yaml_content)
        app_id = str(result.get("app_id") or "")
        status = str(result.get("status") or "")
        if status == "pending" or not app_id:
            raise DifyApiError(
                f"DSL 导入未直接完成（status={status or 'unknown'}）："
                "可能存在依赖插件未安装，请在 Dify 控制台确认导入",
                detail=str(result)[:300],
            )
        api_key = await _ensure_api_key(client, app_id)
    get_dify_store().update_app(app_id, name=summary["app_name"], api_key=api_key)
    return {
        "ok": True,
        "app_id": app_id,
        "name": summary["app_name"],
        "mode": summary["mode"],
        "api_key_saved": bool(api_key),
        "message": "DSL 导入成功，API Key 已保存；工作流类应用记得 dify_publish 发布后才可运行",
    }


async def apply_dsl(ref: str, yaml_content: str, publish: bool = False) -> Dict[str, Any]:
    """把 DSL 内容覆盖到既有应用（工作流类 → draft 同步；对话/补全类 → model-config）。

    返回变更统计；publish=True 时随后发布（仅工作流类应用有发布概念）。
    """
    summary = validate_dsl(yaml_content)
    data = parse_dsl(yaml_content)
    async with console_client() as client:
        await _ensure_admin_session(client)
        app = await _resolve_app(client, ref)
        app_id = str(app["id"])
        mode = str(app.get("mode") or "")
        if summary["mode"] and summary["mode"] != mode:
            raise DifyStateError(
                f"DSL 模式（{summary['mode']}）与目标应用模式（{mode}）不一致，不能覆盖；"
                "如需更换模式请用 dify_import_dsl 新建应用"
            )

        old_dsl = ""
        try:
            old_dsl = await client.export_dsl(app_id)
        except DifyApiError:
            pass

        if mode in _WORKFLOW_MODES:
            workflow = data.get("workflow")
            if not isinstance(workflow, dict) or not isinstance(workflow.get("graph"), dict):
                raise DifyStateError("DSL 缺少 workflow.graph（工作流类应用必须包含完整图定义）")
            await _sync_workflow_draft(client, app_id, workflow)
        else:
            model_config = data.get("model_config")
            if not isinstance(model_config, dict):
                raise DifyStateError("DSL 缺少 model_config（chat/completion 类应用必须包含模型配置）")
            await client.request("POST", f"/apps/{app_id}/model-config",
                                 json_body=model_config, action="同步模型配置")

        published = False
        if publish and mode in _WORKFLOW_MODES:
            await client.publish_workflow(app_id, marked_comment="via AnelfAgent dify_apply_dsl")
            published = True

    diff = dsl_diff_stat(old_dsl, yaml_content) if old_dsl else {"changed": True}
    return {
        "ok": True,
        "app_id": app_id,
        "mode": mode,
        "diff": diff,
        "published": published,
        "message": "DSL 已覆盖到应用草稿" + ("并发布" if published else "（未发布，dify_publish 可发布）"),
    }


async def _sync_workflow_draft(client: DifyConsoleClient, app_id: str,
                               workflow: Dict[str, Any]) -> None:
    """把 DSL 的 workflow 段同步为既有应用的草稿（graph/features/环境变量）。"""
    draft: Dict[str, Any] = {}
    try:
        current = await client.request("GET", f"/apps/{app_id}/workflows/draft",
                                       action="读取当前草稿")
        if isinstance(current, dict):
            draft = current
    except DifyApiError:
        pass

    env_vars = workflow.get("environment_variables")
    env_patch = None
    if isinstance(env_vars, list) and env_vars:
        # 服务端要求 patch 内每个变量带稳定 id；沿用当前草稿中的 id（按名字匹配），
        # 新变量生成 UUID，保证幂等合并而非整表替换
        import uuid as _uuid
        existing_by_name = {
            str(v.get("name")): str(v.get("id"))
            for v in (draft.get("environment_variables") or [])
            if isinstance(v, dict) and v.get("name") and v.get("id")
        }
        patched = []
        for var in env_vars:
            if not isinstance(var, dict):
                continue
            item = dict(var)
            name = str(item.get("name") or "")
            item["id"] = existing_by_name.get(name) or str(_uuid.uuid4())
            patched.append(item)
        if patched:
            env_patch = {
                "environment_variables": patched,
                "deleted_environment_variable_ids": [],
            }

    payload: Dict[str, Any] = {
        "graph": workflow["graph"],
        "features": workflow.get("features") or draft.get("features") or {},
        "hash": draft.get("hash"),
        "conversation_variables": workflow.get("conversation_variables")
                                  or draft.get("conversation_variables") or [],
    }
    if env_patch:
        payload["environment_variable_patch"] = env_patch
    await client.request("POST", f"/apps/{app_id}/workflows/draft",
                         json_body=payload, action="同步工作流草稿")


async def publish(ref: str, marked_name: str = "", marked_comment: str = "") -> Dict[str, Any]:
    """发布应用当前草稿（工作流类应用）。"""
    async with console_client() as client:
        await _ensure_admin_session(client)
        app = await _resolve_app(client, ref)
        mode = str(app.get("mode") or "")
        if mode not in _WORKFLOW_MODES:
            raise DifyStateError(f"应用模式 {mode} 无发布流程（chat/completion 保存即生效）")
        await client.publish_workflow(str(app["id"]),
                                      marked_name=marked_name.strip(),
                                      marked_comment=marked_comment.strip())
    return {"ok": True, "app_id": str(app["id"]), "name": app.get("name", ""),
            "message": "工作流已发布，线上运行即使用新版本"}


# ------------------------------------------------------------------
# 运行调用（Service API）
# ------------------------------------------------------------------


async def _service_client(client: DifyConsoleClient, ref: str) -> tuple[DifyServiceClient, Dict[str, Any]]:
    """解析应用并确保有运行时 Key，返回 Service 客户端与应用记录。"""
    app = await _resolve_app(client, ref)
    app_id = str(app["id"])
    api_key = await _ensure_api_key(client, app_id)
    get_dify_store().update_app(app_id, name=str(app.get("name") or ""), api_key=api_key)
    return DifyServiceClient(get_settings()["base_url"], api_key), app


async def run_workflow(ref: str, inputs: Dict[str, Any], user: str = "anelf-agent") -> Dict[str, Any]:
    """运行工作流（blocking，同步返回结果）。"""
    if not isinstance(inputs, dict):
        raise DifyStateError("inputs 必须是 JSON 对象")
    async with console_client() as console:
        await _ensure_admin_session(console)
        svc, app = await _service_client(console, ref)
    try:
        result = await svc.run_workflow(inputs, user=user)
    except DifyNotFoundError:
        raise DifyStateError("工作流不可运行：应用可能未发布（dify_publish）或 API 未开通") from None
    wf_data = result.get("data") or {}
    return {
        "ok": True,
        "app_id": str(app["id"]),
        "workflow_run_id": result.get("workflow_run_id") or result.get("id") or "",
        "status": wf_data.get("status", ""),
        "outputs": wf_data.get("outputs"),
        "error": wf_data.get("error") or "",
        "elapsed_time": wf_data.get("elapsed_time"),
        "total_tokens": wf_data.get("total_tokens"),
    }


async def chat(ref: str, query: str, inputs: Optional[Dict[str, Any]] = None,
               conversation_id: str = "", user: str = "anelf-agent") -> Dict[str, Any]:
    """向对话型应用发送消息（blocking，返回 answer 与 conversation_id）。"""
    query = query.strip()
    if not query:
        raise DifyStateError("query 不能为空")
    async with console_client() as console:
        await _ensure_admin_session(console)
        svc, app = await _service_client(console, ref)
    result = await svc.chat(query, inputs=inputs, conversation_id=conversation_id, user=user)
    return {
        "ok": True,
        "app_id": str(app["id"]),
        "message_id": result.get("message_id") or result.get("id") or "",
        "conversation_id": result.get("conversation_id") or "",
        "answer": result.get("answer") or "",
        "usage": result.get("metadata", {}).get("usage") if isinstance(result.get("metadata"), dict) else None,
    }


# ------------------------------------------------------------------
# API Key 管理
# ------------------------------------------------------------------


async def list_api_keys(ref: str) -> Dict[str, Any]:
    async with console_client() as client:
        await _ensure_admin_session(client)
        app = await _resolve_app(client, ref)
        keys = await client.list_api_keys(str(app["id"]))
    items = keys.get("data") if isinstance(keys, dict) else []
    masked = [
        {
            "id": str(k.get("id") or ""),
            "token_preview": (str(k.get("token") or "")[:10] + "…") if k.get("token") else "",
            "last_used_at": k.get("last_used_at"),
        }
        for k in (items if isinstance(items, list) else [])
        if isinstance(k, dict)
    ]
    return {"ok": True, "app_id": str(app["id"]), "keys": masked}


async def create_api_key(ref: str) -> Dict[str, Any]:
    async with console_client() as client:
        await _ensure_admin_session(client)
        app = await _resolve_app(client, ref)
        app_id = str(app["id"])
        created = await client.create_api_key(app_id)
    token = str(created.get("token") or "")
    if token:
        # 新签发的 Key 成为本地默认运行时 Key
        get_dify_store().update_app(app_id, api_key=token,
                                    api_key_id=str(created.get("id") or ""))
    return {"ok": True, "app_id": app_id, "api_key_saved": bool(token),
            "message": "API Key 已签发并保存为本地默认运行时 Key"}


async def revoke_api_key(ref: str, api_key_id: str) -> Dict[str, Any]:
    async with console_client() as client:
        await _ensure_admin_session(client)
        app = await _resolve_app(client, ref)
        await client.delete_api_key(str(app["id"]), api_key_id.strip())
    return {"ok": True, "app_id": str(app["id"]), "revoked": api_key_id.strip()}


# ------------------------------------------------------------------
# 模型供应商 / 数据集
# ------------------------------------------------------------------


async def list_model_providers() -> Dict[str, Any]:
    async with console_client() as client:
        await _ensure_admin_session(client)
        data = await client.list_model_providers()
    providers = data.get("data") if isinstance(data, dict) else data
    brief = []
    for p in (providers if isinstance(providers, list) else []):
        if not isinstance(p, dict):
            continue
        brief.append({
            "provider": p.get("provider", ""),
            "label": (p.get("label") or {}).get("zh_Hans")
                     or (p.get("label") or {}).get("en_US") or p.get("provider", ""),
            "has_credential": bool(p.get("custom_credential_status") == "active"
                                   or p.get("system_credential_status") == "active"),
        })
    return {"ok": True, "count": len(brief), "providers": brief}


async def set_model_credential(provider: str, credentials: Dict[str, Any]) -> Dict[str, Any]:
    """配置模型供应商凭据（如 openai 的 {"openai_api_key": "sk-..."}）。"""
    provider = provider.strip()
    if not provider or not isinstance(credentials, dict) or not credentials:
        raise DifyStateError("provider 与 credentials（非空 JSON 对象）均必填")
    async with console_client() as client:
        await _ensure_admin_session(client)
        await client.set_model_credential(provider, credentials)
    return {"ok": True, "provider": provider,
            "message": "模型供应商凭据已保存（dify_list_model_providers 可确认状态）"}


async def list_datasets() -> Dict[str, Any]:
    async with console_client() as client:
        await _ensure_admin_session(client)
        data = await client.list_datasets()
    items = data.get("data") if isinstance(data, dict) else []
    brief = [
        {"id": str(d.get("id") or ""), "name": d.get("name", ""),
         "doc_count": d.get("document_count"), "word_count": d.get("word_count")}
        for d in (items if isinstance(items, list) else [])
        if isinstance(d, dict)
    ]
    return {"ok": True, "count": len(brief), "datasets": brief}


async def create_dataset(name: str, description: str = "") -> Dict[str, Any]:
    name = name.strip()
    if not name:
        raise DifyStateError("数据集名称不能为空")
    async with console_client() as client:
        await _ensure_admin_session(client)
        created = await client.create_dataset(name, description)
    return {"ok": True, "dataset_id": str(created.get("id") or ""), "name": name,
            "message": "数据集已创建（文档上传与索引请用 Dify 控制台或后续扩展）"}


# ------------------------------------------------------------------
# MCP 桥接（Dify App → Anelf MCP）
# ------------------------------------------------------------------


def _mcp_store():
    """构造挂接 Anelf MCP bridge 热重载的配置存取实例。"""
    from entities.mcp.config import MCPServerStore

    def _reload() -> None:
        try:
            from entities.mcp.bridge import get_mcp_bridge
            bridge = get_mcp_bridge()
            if bridge:
                bridge.reload_config()
        except Exception as exc:
            log(f"MCP 桥接热重载失败: {exc}", "WARNING", tag="Dify")

    return MCPServerStore(on_reload=_reload)


def _mcp_entry_name(app_name: str, app_id: str) -> str:
    base = "".join(c if (c.isalnum() or c in "-_") else "-" for c in app_name.strip().lower())
    base = base.strip("-") or "app"
    return f"dify-{base}-{app_id[:8]}"


async def enable_mcp(ref: str, description: str = "") -> Dict[str, Any]:
    """把 Dify 应用暴露为 MCP Server，并桥接进 Anelf（工具自动注册可调用）。"""
    async with console_client() as client:
        await _ensure_admin_session(client)
        app = await _resolve_app(client, ref)
        app_id = str(app["id"])
        result = await client.enable_mcp_server(
            app_id, description=description.strip(),
            parameters={},
        )
    server_code = str(result.get("server_code") or "")
    if not server_code:
        raise DifyApiError("启用 MCP Server 响应缺少 server_code")
    server_id = str(result.get("id") or "")

    base_url = get_settings()["base_url"]
    mcp_url = f"{base_url}/mcp/server/{server_code}/mcp"
    entry_name = _mcp_entry_name(str(app.get("name") or ""), app_id)
    store = get_dify_store()
    store.update_app(app_id, name=str(app.get("name") or ""),
                     mcp_server_code=server_code, mcp_server_id=server_id)

    try:
        _mcp_store().update_server_config(
            entry_name,
            {"url": mcp_url, "transport": "streamable_http", "enabled": True},
            create_if_missing=True,
        )
        bridged = True
    except Exception as exc:
        log(f"MCP 桥接写入失败: {exc}", "WARNING", tag="Dify")
        bridged = False

    return {
        "ok": True,
        "app_id": app_id,
        "mcp_url": mcp_url,
        "mcp_server_name": entry_name if bridged else "",
        "bridged_to_anelf": bridged,
        "message": ("MCP Server 已启用并桥接进 Anelf：该应用的能力已作为 MCP 工具注册，"
                    "后续对话中可直接调用" if bridged else
                    "MCP Server 已启用，但写入 Anelf MCP 配置失败，可在 MCP 页面手动添加该 URL"),
    }


async def disable_mcp(ref: str) -> Dict[str, Any]:
    """停用应用的 MCP Server 并移除 Anelf 侧桥接。"""
    async with console_client() as client:
        await _ensure_admin_session(client)
        app = await _resolve_app(client, ref)
        app_id = str(app["id"])
        await client.disable_mcp_server(app_id)
    removed = await _unbridge_mcp(app_id)
    return {"ok": True, "app_id": app_id, "bridge_removed": removed,
            "message": "MCP Server 已停用" + ("，Anelf 侧桥接已移除" if removed else "")}


async def _unbridge_mcp(app_id: str) -> bool:
    """移除 Anelf MCP 配置中该应用对应的 server 条目。"""
    local = get_dify_store().get_app(app_id)
    app_name = str(local.get("name") or "")
    entry_name = _mcp_entry_name(app_name, app_id) if app_name else ""
    candidates = {entry_name} if entry_name else set()
    try:
        store = _mcp_store()
        names = set(store.get_server_names())
        for name in candidates | {n for n in names if n.endswith(app_id[:8]) and n.startswith("dify-")}:
            if name in names:
                store.remove_server(name)
                return True
    except Exception as exc:
        log(f"移除 MCP 桥接失败: {exc}", "WARNING", tag="Dify")
    return False


async def get_mcp_status(ref: str) -> Dict[str, Any]:
    """查询应用的 MCP Server 状态与桥接情况。"""
    async with console_client() as client:
        await _ensure_admin_session(client)
        app = await _resolve_app(client, ref)
        app_id = str(app["id"])
        try:
            server = await client.get_mcp_server(app_id)
        except DifyNotFoundError:
            server = None
    local = get_dify_store().get_app(app_id)
    code = local.get("mcp_server_code", "")
    return {
        "ok": True,
        "app_id": app_id,
        "enabled": bool(server),
        "mcp_url": f"{get_settings()['base_url']}/mcp/server/{code}/mcp" if code else "",
        "server": server if isinstance(server, dict) else None,
    }


# ------------------------------------------------------------------
# 概览（上下文注入 / AI 决策参考）
# ------------------------------------------------------------------


async def console_overview() -> Dict[str, Any]:
    """控制台概览：应用数/数据集数/应用清单摘要。"""
    status = await get_status()
    if not status["reachable"] or not status["admin_configured"]:
        return {"ok": True, "reachable": status["reachable"],
                "admin_configured": status["admin_configured"],
                "message": "Dify 不可达或未配置管理员，无法获取概览（先 dify_connect）"}
    async with console_client() as client:
        await _ensure_admin_session(client)
        apps = await _fetch_apps(client)
        datasets = await client.list_datasets()
    ds_items = datasets.get("data") if isinstance(datasets, dict) else []
    return {
        "ok": True,
        "reachable": True,
        "version": status["version"],
        "app_count": len(apps),
        "dataset_count": len(ds_items) if isinstance(ds_items, list) else 0,
        "apps": [{"id": str(a.get("id") or ""), "name": a.get("name", ""),
                  "mode": a.get("mode", "")} for a in apps[:20]],
    }


class DifyService:
    """生命周期容器（httpx 无长驻连接，当前仅为清理占位与状态归属）。"""

    async def aclose(self) -> None:
        """进程退出清理（无长驻资源，保留接口对齐 Lifecycle 约定）。"""


_service_instance: Optional[DifyService] = None


def get_dify_service() -> DifyService:
    global _service_instance
    if _service_instance is None:
        _service_instance = DifyService()
    return _service_instance
