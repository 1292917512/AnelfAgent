"""SillyTavern 酒馆管理工具（AI 可调用）。

分组 sillytavern；运行状态感知由 context.py 的动态注入承担，
工具无需 always 标签。所有酒馆 API 操作要求酒馆处于运行状态，
未运行时返回带修复指引的工具错误。
"""

from __future__ import annotations

import json
from typing import Any, Dict

from core.tool_errors import ErrorCause, tool_error
from entities._sdk import tool

from . import chat_bridge, git_ops, service
from . import config as st_config
from .st_client import STError, get_st_client

_GROUP = "sillytavern"


def _require_running() -> str:
    """返回 base_url；酒馆未运行时抛出 ValueError（转工具错误）。"""
    if not service.is_running():
        raise RuntimeError("酒馆当前未运行。先用 sillytavern_start 启动它。")
    return st_config.base_url()


def _guard(fn) -> str:
    """统一异常 → 工具错误 JSON。"""
    try:
        result = fn()
        return json.dumps(result, ensure_ascii=False, default=str)
    except STError as e:
        return tool_error(str(e), cause=ErrorCause.NETWORK,
                          hint="确认酒馆在运行: sillytavern_status", retryable=True)
    except (RuntimeError, ValueError) as e:
        return tool_error(str(e), cause=ErrorCause.STATE, hint=str(e), retryable=False)


# ------------------------------------------------------------------
# 生命周期
# ------------------------------------------------------------------

@tool(name="sillytavern_status", group=_GROUP, tags=["sillytavern"],
      concurrency_safe=True,
      description="查询酒馆(SillyTavern)运行状态：是否运行、地址、端口、版本、PID、启动时间")
def sillytavern_status() -> str:
    return json.dumps(service.status(), ensure_ascii=False, default=str)


@tool(name="sillytavern_start", group=_GROUP, tags=["sillytavern"],
      description="启动酒馆服务（等待就绪后返回地址）。已运行则直接返回状态")
def sillytavern_start() -> str:
    return _guard(service.start)


@tool(name="sillytavern_stop", group=_GROUP, tags=["sillytavern"],
      description="停止酒馆服务（SIGTERM 优雅停止，超时强制终止）")
def sillytavern_stop() -> str:
    return _guard(service.stop)


@tool(name="sillytavern_restart", group=_GROUP, tags=["sillytavern"],
      description="重启酒馆服务")
def sillytavern_restart() -> str:
    return _guard(service.restart)


@tool(name="sillytavern_logs", group=_GROUP, tags=["sillytavern"],
      concurrency_safe=True,
      description="查看酒馆最近运行日志（尾部）")
def sillytavern_logs(max_chars: int = 2000) -> str:
    if max_chars <= 0 or max_chars > 20000:
        max_chars = 2000
    return json.dumps({"log_tail": service.tail_log(max_chars)}, ensure_ascii=False)


# ------------------------------------------------------------------
# Git 更新与二次开发提交
# ------------------------------------------------------------------

@tool(name="sillytavern_update", group=_GROUP, tags=["sillytavern"],
      description="拉取酒馆源码更新（git pull --ff-only，默认 origin 即用户 fork；"
                  "同步官方可传 remote='upstream'）。酒馆运行中会提示先重启")
def sillytavern_update(remote: str = "origin", branch: str = "") -> str:
    def _run() -> Dict[str, Any]:
        running = service.is_running()
        result = git_ops.pull(remote, branch or None)
        result["running_before_update"] = running
        result["hint"] = "源码已更新，建议 sillytavern_restart 让新代码生效" if running else ""
        return result
    return _guard(_run)


@tool(name="sillytavern_switch_version", group=_GROUP, tags=["sillytavern"],
      description="切换酒馆源码到指定远端分支版本（如 release/staging/main）。"
                  "工作区有未提交修改时会拒绝执行，需先 sillytavern_commit")
def sillytavern_switch_version(name: str, remote: str = "origin") -> str:
    def _run() -> Dict[str, Any]:
        return git_ops.checkout_version(remote, name)
    return _guard(_run)


@tool(name="sillytavern_commit", group=_GROUP, tags=["sillytavern"],
      description="提交酒馆二次开发修改并推送到 origin（git add -A + commit + push）")
def sillytavern_commit(message: str) -> str:
    def _run() -> Dict[str, Any]:
        if not message or not message.strip():
            raise ValueError("提交信息不能为空")
        return git_ops.commit_push(message)
    return _guard(_run)


@tool(name="sillytavern_git_status", group=_GROUP, tags=["sillytavern"],
      concurrency_safe=True,
      description="查看酒馆源码仓库状态：分支、最新提交、未提交修改")
def sillytavern_git_status() -> str:
    return _guard(git_ops.status)


# ------------------------------------------------------------------
# 角色管理
# ------------------------------------------------------------------

@tool(name="sillytavern_list_characters", group=_GROUP, tags=["sillytavern"],
      concurrency_safe=True,
      description="列出酒馆中所有角色卡（名称、简介、标签等摘要）")
def sillytavern_list_characters() -> str:
    def _run() -> Dict[str, Any]:
        base = _require_running()
        chars = get_st_client().characters_all(base)
        return {"count": len(chars),
                "characters": [get_st_client().character_brief(c) for c in chars]}
    return _guard(_run)


@tool(name="sillytavern_get_character", group=_GROUP, tags=["sillytavern"],
      concurrency_safe=True,
      description="查看指定角色的完整人设卡（description/personality/first_mes 等）")
def sillytavern_get_character(avatar: str) -> str:
    def _run() -> Dict[str, Any]:
        base = _require_running()
        return get_st_client().get_character(base, avatar)
    return _guard(_run)


@tool(name="sillytavern_create_character", group=_GROUP, tags=["sillytavern"],
      description="在酒馆创建新角色卡。name 必填；description=人设描述、"
                  "personality=性格、first_mes=开场白、scenario=场景、"
                  "mes_example=对话示例、system_prompt=角色专属系统提示")
def sillytavern_create_character(
    name: str,
    description: str = "",
    personality: str = "",
    first_mes: str = "",
    scenario: str = "",
    mes_example: str = "",
    system_prompt: str = "",
    tags: str = "",
) -> str:
    def _run() -> Dict[str, Any]:
        base = _require_running()
        if not name.strip():
            raise ValueError("角色名不能为空")
        fields: Dict[str, Any] = {"name": name.strip()}
        for key, val in (("description", description), ("personality", personality),
                         ("first_mes", first_mes), ("scenario", scenario),
                         ("mes_example", mes_example), ("system_prompt", system_prompt)):
            if str(val).strip():
                fields[key] = str(val)
        if tags.strip():
            fields["tags"] = [t.strip() for t in tags.split(",") if t.strip()]
        avatar = get_st_client().create_character(base, fields)
        return {"ok": True, "avatar": avatar,
                "hint": f"角色已创建，avatar 标识为 {avatar}，后续操作用它引用"}
    return _guard(_run)


@tool(name="sillytavern_edit_character", group=_GROUP, tags=["sillytavern"],
      description="修改角色卡的单个字段。field 可选: description / personality / "
                  "first_mes / scenario / mes_example / system_prompt 等角色卡字段")
def sillytavern_edit_character(avatar: str, field: str, value: str,
                               current_name: str = "") -> str:
    def _run() -> Dict[str, Any]:
        base = _require_running()
        result = get_st_client().edit_character_field(
            base, avatar, current_name or avatar.rsplit(".", 1)[0], field, value)
        return {"ok": True, "avatar": avatar, "field": field, "result": result}
    return _guard(_run)


@tool(name="sillytavern_delete_character", group=_GROUP, tags=["sillytavern"],
      description="删除角色卡（delete_chats=True 时连同其聊天记录一起删除，不可恢复）")
def sillytavern_delete_character(avatar: str, delete_chats: bool = False) -> str:
    def _run() -> Dict[str, Any]:
        base = _require_running()
        get_st_client().delete_character(base, avatar, delete_chats)
        return {"ok": True, "deleted": avatar}
    return _guard(_run)


# ------------------------------------------------------------------
# 模型配置
# ------------------------------------------------------------------

# oai_settings 中暴露给 AI/前端的关键模型参数（酒馆按 main_api 分区存储）
_MODEL_PARAM_KEYS = [
    "temp_openai", "freq_pen_openai", "pres_pen_openai", "top_p_openai",
    "openai_max_context", "openai_max_tokens", "stream_openai",
]
_MAIN_APIS = ["openai", "textgenerationwebui", "novelai", "koboldhorde",
              "kobold", "textgenerationwebui-ooba", "openrouter"]


@tool(name="sillytavern_get_model_config", group=_GROUP, tags=["sillytavern"],
      concurrency_safe=True,
      description="读取酒馆当前模型配置：API 类型(main_api)、模型名、温度、"
                  "上下文长度、生成上限等关键参数")
def sillytavern_get_model_config() -> str:
    def _run() -> Dict[str, Any]:
        base = _require_running()
        settings = get_st_client().get_settings(base)["settings"]
        oai = settings.get("oai_settings", {}) or {}
        summary = {
            "main_api": settings.get("main_api"),
            "api_server": settings.get("api_server"),
            "username": settings.get("username"),
            "model": oai.get("openai_model") or settings.get("model"),
        }
        for key in _MODEL_PARAM_KEYS:
            if key in oai:
                summary[key] = oai[key]
        return summary
    return _guard(_run)


@tool(name="sillytavern_set_model_config", group=_GROUP, tags=["sillytavern"],
      description="修改酒馆模型配置。main_api=API类型(openai/textgenerationwebui等)，"
                  "model=模型名，temperature=温度(0-2)，max_context=上下文 tokens，"
                  "max_tokens=单次生成上限。只传要改的参数")
def sillytavern_set_model_config(
    main_api: str = "",
    model: str = "",
    temperature: float = 0.0,
    max_context: int = 0,
    max_tokens: int = 0,
) -> str:
    def _run() -> Dict[str, Any]:
        base = _require_running()
        client = get_st_client()
        settings = client.get_settings(base)["settings"]
        changed: Dict[str, Any] = {}
        if main_api:
            if main_api not in _MAIN_APIS:
                raise ValueError(f"不支持的 main_api: {main_api}（可选: {', '.join(_MAIN_APIS)}）")
            settings["main_api"] = main_api
            changed["main_api"] = main_api
        oai = dict(settings.get("oai_settings", {}) or {})
        if model:
            oai["openai_model"] = model
            changed["model"] = model
        if temperature > 0:
            oai["temp_openai"] = min(temperature, 2.0)
            changed["temperature"] = oai["temp_openai"]
        if max_context > 0:
            oai["openai_max_context"] = max_context
            changed["max_context"] = max_context
        if max_tokens > 0:
            oai["openai_max_tokens"] = max_tokens
            changed["max_tokens"] = max_tokens
        if not changed:
            raise ValueError("未指定任何要修改的参数")
        settings["oai_settings"] = oai
        client.save_settings(base, settings)
        return {"ok": True, "changed": changed,
                "hint": "配置已写入，酒馆网页端需刷新后生效"}
    return _guard(_run)


# ------------------------------------------------------------------
# 聊天记录
# ------------------------------------------------------------------

@tool(name="sillytavern_list_chats", group=_GROUP, tags=["sillytavern"],
      concurrency_safe=True,
      description="列出指定角色的所有聊天文件")
def sillytavern_list_chats(avatar: str) -> str:
    def _run() -> Dict[str, Any]:
        base = _require_running()
        chats = get_st_client().character_chats(base, avatar)
        return {"avatar": avatar, "chats": chats}
    return _guard(_run)


@tool(name="sillytavern_read_chat", group=_GROUP, tags=["sillytavern"],
      concurrency_safe=True,
      description="读取指定角色的一份聊天记录（messages 数组）")
def sillytavern_read_chat(avatar: str, file_name: str, max_messages: int = 50) -> str:
    def _run() -> Dict[str, Any]:
        base = _require_running()
        messages = get_st_client().get_chat(base, avatar, file_name)
        trimmed = messages[:max_messages] if max_messages > 0 else messages
        return {"file": file_name, "total": len(messages), "messages": trimmed}
    return _guard(_run)


# ------------------------------------------------------------------
# AI 与酒馆角色对话（AI 是独立参与者，经 anelf-bridge 插件走酒馆生成管道）
# ------------------------------------------------------------------

@tool(name="sillytavern_overview", group=_GROUP, tags=["sillytavern"],
      concurrency_safe=True,
      description="酒馆体检：一次拿到酒馆全貌——运行状态、版本、当前模型配置、"
                  "角色数、世界书/预设/扩展清单、最近聊天、桥接插件是否就绪、"
                  "git 分支与未提交修改。想全面了解酒馆或排障时先调用它")
def sillytavern_overview() -> str:
    def _run() -> Dict[str, Any]:
        st = service.status()
        out: Dict[str, Any] = {"status": st, "running": st["running"]}
        if not st["running"]:
            out["hint"] = "酒馆未运行。sillytavern_start 可启动；启动后我再给你完整全貌。"
            # 离线时也给 git 状态（不依赖酒馆运行）
            try:
                out["git"] = git_ops.status()
            except Exception:
                pass
            return out

        base = st_config.base_url()
        client = get_st_client()

        # 模型配置
        try:
            settings = client.get_settings(base)["settings"]
            oai = settings.get("oai_settings", {}) or {}
            out["model"] = {
                "main_api": settings.get("main_api"),
                "source": oai.get("chat_completion_source"),
                "model": oai.get("openai_model") or settings.get("model"),
                "temperature": oai.get("temp_openai"),
                "max_context": oai.get("openai_max_context"),
                "custom_url": oai.get("custom_url") or None,
            }
            out["world_books"] = client.get_settings(base).get("world_names", [])
            out["presets"] = {
                "instruct": [x.get("name") for x in (client.get_settings(base).get("instruct") or []) if isinstance(x, dict)][:20],
            }
        except STError as e:
            out["model_error"] = str(e)

        # 角色与聊天
        try:
            chars = client.characters_all(base)
            out["characters"] = {
                "count": len(chars),
                "names": [c.get("name") for c in chars][:30],
            }
        except STError:
            pass
        try:
            recent = client.recent_chats(base)
            out["recent_chats"] = [
                {"file": r.get("file_name"),
                 "avatar": r.get("avatar") or r.get("character"),
                 "preview": (r.get("mes") or r.get("last_mes") or "")[:40]}
                for r in (recent or [])[:5]
            ]
        except STError:
            pass

        # 桥接插件与扩展
        try:
            from . import chat_bridge
            out["bridge"] = chat_bridge.bridge_health()
        except Exception as e:
            out["bridge"] = {"ok": False, "error": str(e)}
        try:
            exts = client.extensions_discover(base)
            out["extensions"] = {
                "count": len(exts),
                "names": [e.get("name") for e in exts if isinstance(e, dict)][:30],
            }
        except STError:
            pass

        # git
        try:
            g = git_ops.status()
            out["git"] = {"branch": g["branch"], "last_commit": g["last_commit"],
                          "dirty_count": g["dirty_count"]}
        except Exception:
            pass

        out["hint"] = (
            "酒馆运行中。可用工具：sillytavern_chat（对话）、角色管理、"
            "sillytavern_set_model_config（调模型）、sillytavern_update/switch_version（源码）、"
            "sillytavern_restart（改动后生效）。")
        return out
    return _guard(_run)


@tool(name="sillytavern_chat", group=_GROUP, tags=["sillytavern"],
      description="以你自己的身份直接对酒馆里的某个角色说话。消息会注入酒馆会话，"
                  "由酒馆用已配置的模型生成角色回复并写入聊天记录（可在酒馆网页查看）。"
                  "name 是你在酒馆中显示的名字；chat_file 不填则续写当天的聊天")
def sillytavern_chat(avatar: str, message: str, chat_file: str = "",
                     name: str = "Anelf") -> str:
    def _run() -> Dict[str, Any]:
        return chat_bridge.chat_turn(avatar, message, chat_file or None, name)
    return _guard(_run)


# ------------------------------------------------------------------
# 世界书（Lorebook）
# ------------------------------------------------------------------

@tool(name="sillytavern_list_worldbooks", group=_GROUP, tags=["sillytavern"],
      concurrency_safe=True,
      description="列出酒馆里所有世界书（Lorebook）名称")
def sillytavern_list_worldbooks() -> str:
    def _run() -> Dict[str, Any]:
        base = _require_running()
        books = get_st_client().worldinfo_list(base)
        return {"count": len(books),
                "books": [{"name": b.get("name"), "file_id": b.get("file_id")}
                          for b in books]}
    return _guard(_run)


@tool(name="sillytavern_get_worldbook", group=_GROUP, tags=["sillytavern"],
      concurrency_safe=True,
      description="读取指定世界书的完整内容（条目列表）")
def sillytavern_get_worldbook(name: str) -> str:
    def _run() -> Dict[str, Any]:
        base = _require_running()
        return get_st_client().worldinfo_get(base, name)
    return _guard(_run)


# ------------------------------------------------------------------
# 向量记忆（Data Bank / 角色长期记忆）
# ------------------------------------------------------------------

@tool(name="sillytavern_memory_insert", group=_GROUP, tags=["sillytavern"],
      description="往酒馆的向量记忆库写入一段文本。collection_id 用角色绑定时是 "
                  "'chat_<avatar去扩展名>'（如 chat_Seraphina），自由知识库用任意名字。"
                  "注意：默认 embedding 源(transformers)需联网下载模型，离线环境会失败")
def sillytavern_memory_insert(collection_id: str, text: str) -> str:
    def _run() -> Dict[str, Any]:
        base = _require_running()
        if not text.strip():
            raise ValueError("text 不能为空")
        try:
            get_st_client().vector_insert(base, text.strip(), collection_id)
        except STError as e:
            if "500" in str(e) or "400" in str(e):
                raise RuntimeError(
                    f"向量写入失败: {e}。默认 embedding 源(transformers)需联网下载模型，"
                    "若离线请在酒馆设置里改用 OpenAI/SiliconFlow 等在线 embedding 源。") from e
            raise
        return {"ok": True, "collection_id": collection_id}
    return _guard(_run)


@tool(name="sillytavern_memory_query", group=_GROUP, tags=["sillytavern"],
      concurrency_safe=True,
      description="在酒馆向量记忆库做相似度检索，返回最相关的若干条")
def sillytavern_memory_query(collection_id: str, query: str, top_k: int = 5) -> str:
    def _run() -> Dict[str, Any]:
        base = _require_running()
        result = get_st_client().vector_query(base, collection_id, query, top_k)
        return {"collection_id": collection_id, "results": result}
    return _guard(_run)


@tool(name="sillytavern_memory_list", group=_GROUP, tags=["sillytavern"],
      concurrency_safe=True,
      description="列出某个向量记忆库里的条目")
def sillytavern_memory_list(collection_id: str) -> str:
    def _run() -> Dict[str, Any]:
        base = _require_running()
        return {"collection_id": collection_id,
                "items": get_st_client().vector_list(base, collection_id)}
    return _guard(_run)


# ------------------------------------------------------------------
# 群聊
# ------------------------------------------------------------------

@tool(name="sillytavern_list_groups", group=_GROUP, tags=["sillytavern"],
      concurrency_safe=True,
      description="列出酒馆里所有群聊及其成员")
def sillytavern_list_groups() -> str:
    def _run() -> Dict[str, Any]:
        base = _require_running()
        groups = get_st_client().groups_all(base)
        return {"count": len(groups), "groups": [
            {"id": g.get("id"), "name": g.get("name"),
             "members": g.get("members", [])} for g in groups]}
    return _guard(_run)


# ------------------------------------------------------------------
# 外部角色卡导入
# ------------------------------------------------------------------

@tool(name="sillytavern_import_character", group=_GROUP, tags=["sillytavern"],
      description="从 URL 导入角色卡或世界书（支持 chub.ai、janitorai 等）。"
                  "返回导入后的文件标识")
def sillytavern_import_character(url: str) -> str:
    def _run() -> Dict[str, Any]:
        base = _require_running()
        if not url.strip():
            raise ValueError("url 不能为空")
        return get_st_client().content_import_url(base, url.strip())
    return _guard(_run)


# ------------------------------------------------------------------
# 备份 / 统计
# ------------------------------------------------------------------

@tool(name="sillytavern_backup", group=_GROUP, tags=["sillytavern"],
      description="打包酒馆当前用户的全部数据（完整备份）。会下载数据包，"
                  "用于更新源码或大改前留底")
def sillytavern_backup() -> str:
    def _run() -> Dict[str, Any]:
        base = _require_running()
        result = get_st_client().user_backup(base)
        return {"ok": True, "backup": result,
                "hint": "完整数据包已生成；建议也把 data/ 目录做外部快照"}
    return _guard(_run)


@tool(name="sillytavern_stats", group=_GROUP, tags=["sillytavern"],
      concurrency_safe=True,
      description="读取酒馆用量统计（各模型的 token 消耗、生成次数等）")
def sillytavern_stats() -> str:
    def _run() -> Dict[str, Any]:
        base = _require_running()
        return get_st_client().stats_get(base)
    return _guard(_run)


# ------------------------------------------------------------------
# 扩展管理（只读列举 + 安装需人工确认，防供应链风险）
# ------------------------------------------------------------------

@tool(name="sillytavern_list_extensions", group=_GROUP, tags=["sillytavern"],
      concurrency_safe=True,
      description="列出酒馆已安装的扩展（名称/版本/来源 global 或 local）")
def sillytavern_list_extensions() -> str:
    def _run() -> Dict[str, Any]:
        base = _require_running()
        exts = get_st_client().extensions_discover(base)
        return {"count": len(exts), "extensions": [
            {"name": e.get("name"), "type": e.get("type"),
             "version": e.get("version")} for e in exts]}
    return _guard(_run)


@tool(name="sillytavern_install_extension", group=_GROUP, tags=["sillytavern"],
      description="安装酒馆扩展（git URL，默认装到全局 global，全局=酒馆主程序共享，"
                  "local=仅当前用户）。装完用 sillytavern_list_extensions 可见，"
                  "建议 sillytavern_restart 后生效")
def sillytavern_install_extension(url: str, global_install: bool = True) -> str:
    def _run() -> Dict[str, Any]:
        base = _require_running()
        if not url.strip():
            raise ValueError("url 不能为空")
        result = get_st_client().extension_install(base, url.strip(), global_install)
        return {"ok": True, "url": url, "global": global_install, "result": result,
                "hint": "扩展已安装，建议 sillytavern_restart 后生效"}
    return _guard(_run)
