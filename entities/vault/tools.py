"""密码本 AI 工具组（group=vault，整组沉睡，activate_tool_group 唤醒）。

解锁模型（AI 优先）：默认机器密钥模式——首次写入自动建库、启动自动解锁，
AI 全程零摩擦；主密码模式为可选加固（Web 面板启用），此时 AI 需主人提供
主密码经 vault_unlock 解锁。

安全纪律：
- ``vault_ai_enabled`` 总开关（实体配置）；锁定/未初始化返回 STATE 错误并引导
- vault_reveal / vault_totp / vault_delete 标记 risk=CRITICAL，
  建议主人在审批规则页配置 ask 规则（pattern 直接用工具名）：
  - vault_reveal → ask / critical（读取明文密码）
  - vault_totp → ask / critical（读取动态验证码）
  - vault_delete → ask / critical（删除条目）
- 全局 sanitizer 会兜底遮盖工具结果中的密码赋值模式（纵深防御，有意设计）
"""

from __future__ import annotations

import json
from typing import Any, Dict

from core.config import get_config_bool, get_config_int
from entities._sdk import ErrorCause, error_from_exception, tool, tool_error

from . import generator
from .service import (
    EntryNotFoundError,
    VaultError,
    VaultNotInitializedError,
    get_vault_service,
)
from .session import VaultLockedError

_SLEEP_BRIEF = "密码本（条目增删改查 / 模糊检索 / TOTP 验证码 / 强密码生成）"

_SENSITIVE_HINT = (
    "建议主人在审批规则页为此工具配置 ask 规则（critical），"
    "防止明文凭据在无人确认时流出"
)


def _gate() -> str:
    """AI 工具总开关（实体配置 vault_ai_enabled）。"""
    if not get_config_bool("vault_ai_enabled", True):
        return tool_error(
            "密码本工具已在实体配置中禁用",
            cause=ErrorCause.STATE, retryable=False,
            hint="在实体详情页配置中开启 vault_ai_enabled")
    return ""


def _locked_gate() -> str:
    """锁定态统一错误文案（供 _map_error 映射 VaultLockedError）。"""
    return tool_error(
        "密码本处于锁定状态（主密码模式）",
        cause=ErrorCause.STATE, retryable=False,
        hint="请主人在对话中提供主密码后用 vault_unlock 解锁，或到 Web 面板解锁")


def _map_error(exc: Exception, action: str) -> str:
    if isinstance(exc, VaultLockedError):
        return _locked_gate()
    if isinstance(exc, EntryNotFoundError):
        return tool_error(str(exc), cause=ErrorCause.NOT_FOUND, retryable=False)
    if isinstance(exc, VaultNotInitializedError):
        return tool_error(
            "密码本尚未初始化", cause=ErrorCause.STATE, retryable=False,
            hint="请主人到 Web 面板的密码本页面设置主密码")
    if isinstance(exc, VaultError):
        return tool_error(str(exc), cause=ErrorCause.PARAM, retryable=False)
    return error_from_exception(exc, action=action)


def _parse_tags(raw: str) -> list[str]:
    return [t.strip() for t in raw.replace("，", ",").split(",") if t.strip()]


@tool(name="vault_status", group="vault", concurrency_safe=True,
      allow_sleep=True, sleep_brief=_SLEEP_BRIEF)
async def vault_status() -> str:
    """查看密码本状态（是否初始化/解锁/条目数/自动锁定剩余时间）。"""
    if error := _gate():
        return error
    try:
        return json.dumps(await get_vault_service().status(), ensure_ascii=False)
    except Exception as exc:
        return _map_error(exc, "查询密码本状态")


@tool(name="vault_unlock", group="vault", allow_sleep=True, sleep_brief=_SLEEP_BRIEF)
async def vault_unlock(password: str = "") -> str:
    """解锁密码本。机器密钥模式（默认）无需密码直接解锁；主密码模式需主人提供主密码。

    Args:
        password: 主密码（仅主密码模式需要；机器模式留空即可）
    """
    if error := _gate():
        return error
    try:
        await get_vault_service().unlock(password)
        return json.dumps(await get_vault_service().status(), ensure_ascii=False)
    except Exception as exc:
        return _map_error(exc, "解锁密码本")


@tool(name="vault_search", group="vault", concurrency_safe=True,
      allow_sleep=True, sleep_brief=_SLEEP_BRIEF)
async def vault_search(query: str = "", tag: str = "",
                       favorite_only: bool = False, limit: int = 10) -> str:
    """模糊检索密码本条目（标题/账号/站点/标签加权打分），返回元数据（不含密码明文）。

    Args:
        query: 检索词（站点名、账号、标题、标签均可；留空则列出全部）
        tag: 按标签精确过滤
        favorite_only: 只看收藏条目
        limit: 返回条数上限
    """
    if error := _gate():
        return error
    try:
        results = await get_vault_service().search(
            query, tag=tag, favorite_only=favorite_only, limit=max(1, min(50, limit)))
        return json.dumps({"count": len(results), "entries": results},
                          ensure_ascii=False)
    except Exception as exc:
        return _map_error(exc, "检索密码本")


@tool(name="vault_get", group="vault", concurrency_safe=True,
      allow_sleep=True, sleep_brief=_SLEEP_BRIEF)
async def vault_get(entry_id: str) -> str:
    """查看单条密码本条目详情（密码以掩码展示；需要明文用 vault_reveal）。

    Args:
        entry_id: 条目 id（vault_search 返回的 id）
    """
    if error := _gate():
        return error
    try:
        return json.dumps(await get_vault_service().masked_entry(entry_id),
                          ensure_ascii=False)
    except Exception as exc:
        return _map_error(exc, "查看密码本条目")


@tool(name="vault_reveal", group="vault", risk="CRITICAL",
      allow_sleep=True, sleep_brief=_SLEEP_BRIEF)
async def vault_reveal(entry_id: str, field: str = "password") -> str:
    """读取条目的明文密码/备注（敏感操作，建议主人配置审批规则）。

    Args:
        entry_id: 条目 id
        field: 要读取的字段（password 或 notes；TOTP 验证码用 vault_totp）
    """
    if error := _gate():
        return error
    try:
        plaintext = await get_vault_service().reveal(entry_id, field)
        return json.dumps({
            "entry_id": entry_id, "field": field, field: plaintext,
            "notice": f"明文凭据仅用于当前任务，禁止写入记忆/便签/文件。{_SENSITIVE_HINT}",
        }, ensure_ascii=False)
    except Exception as exc:
        return _map_error(exc, "读取明文凭据")


@tool(name="vault_totp", group="vault", risk="CRITICAL",
      allow_sleep=True, sleep_brief=_SLEEP_BRIEF)
async def vault_totp(entry_id: str) -> str:
    """获取条目当前的 TOTP 动态验证码（6 位，30 秒有效期，敏感操作）。

    Args:
        entry_id: 条目 id（需已配置 TOTP secret）
    """
    if error := _gate():
        return error
    try:
        return json.dumps(await get_vault_service().totp_code(entry_id),
                          ensure_ascii=False)
    except Exception as exc:
        return _map_error(exc, "获取 TOTP 验证码")


@tool(name="vault_add", group="vault", allow_sleep=True, sleep_brief=_SLEEP_BRIEF)
async def vault_add(title: str, username: str = "", url: str = "",
                    password: str = "", totp_secret: str = "", notes: str = "",
                    tags: str = "", favorite: bool = False,
                    generate: bool = False) -> str:
    """新增密码本条目。设 generate=true 可自动生成强密码写入（未提供 password 时）。

    Args:
        title: 条目标题（如 "GitHub 工作号"，必填）
        username: 登录账号
        url: 站点地址
        password: 密码明文（留空且 generate=false 则只存元数据）
        totp_secret: TOTP 密钥（base32 或 otpauth:// URI，可选）
        notes: 备注
        tags: 逗号分隔的标签
        favorite: 是否收藏
        generate: 未提供 password 时自动生成强密码（长度取配置 vault_generator_default_length）
    """
    if error := _gate():
        return error
    try:
        if generate and not password:
            password = generator.generate_password(
                get_config_int("vault_generator_default_length", 20))
        entry = await get_vault_service().add_entry(
            title=title, username=username, url=url, password=password,
            totp_secret=totp_secret, notes=notes,
            tags=_parse_tags(tags), favorite=favorite)
        out: Dict[str, Any] = {"added": entry}
        if generate and password:
            out["generated_password"] = password
        return json.dumps(out, ensure_ascii=False)
    except Exception as exc:
        return _map_error(exc, "新增密码本条目")


@tool(name="vault_update", group="vault", allow_sleep=True, sleep_brief=_SLEEP_BRIEF)
async def vault_update(entry_id: str, title: str = "", username: str = "",
                       url: str = "", password: str = "", totp_secret: str = "",
                       notes: str = "", tags: str = "", favorite: bool = False,
                       clear_fields: str = "") -> str:
    """更新密码本条目（只更新传入的非空字段；clear_fields 可显式清除字段）。

    Args:
        entry_id: 条目 id
        title: 新标题
        username: 新账号
        url: 新站点地址
        password: 新密码
        totp_secret: 新 TOTP 密钥
        notes: 新备注
        tags: 新标签（逗号分隔，整体替换）
        favorite: 是否收藏
        clear_fields: 要清空的敏感字段（逗号分隔：password,totp,notes）
    """
    if error := _gate():
        return error
    try:
        service = get_vault_service()
        kwargs: Dict[str, Any] = {}
        if title:
            kwargs["title"] = title
        if username:
            kwargs["username"] = username
        if url:
            kwargs["url"] = url
        if password:
            kwargs["password"] = password
        if totp_secret:
            kwargs["totp_secret"] = totp_secret
        if notes:
            kwargs["notes"] = notes
        if tags:
            kwargs["tags"] = _parse_tags(tags)
        if favorite:
            kwargs["favorite"] = favorite
        for name in _parse_tags(clear_fields):
            if name == "password":
                kwargs["password"] = ""
            elif name == "totp":
                kwargs["totp_secret"] = ""
            elif name == "notes":
                kwargs["notes"] = ""
            elif name == "favorite":
                kwargs["favorite"] = False
        entry = await service.update_entry(entry_id, **kwargs)
        return json.dumps({"updated": entry}, ensure_ascii=False)
    except Exception as exc:
        return _map_error(exc, "更新密码本条目")


@tool(name="vault_delete", group="vault", risk="CRITICAL",
      allow_sleep=True, sleep_brief=_SLEEP_BRIEF)
async def vault_delete(entry_id: str) -> str:
    """删除密码本条目（不可恢复，敏感操作）。

    Args:
        entry_id: 条目 id
    """
    if error := _gate():
        return error
    try:
        service = get_vault_service()
        entry = await service.get_entry(entry_id)
        await service.delete_entry(entry_id)
        return json.dumps({"deleted": entry_id, "title": entry["title"]},
                          ensure_ascii=False)
    except Exception as exc:
        return _map_error(exc, "删除密码本条目")


@tool(name="vault_generate", group="vault", concurrency_safe=True,
      allow_sleep=True, sleep_brief=_SLEEP_BRIEF)
async def vault_generate(length: int = 20, symbols: bool = True,
                         exclude_ambiguous: bool = False,
                         memorable: bool = False) -> str:
    """生成加密学安全的随机密码（不落盘；配合 vault_add 写入条目）。

    Args:
        length: 长度（4-128）
        symbols: 是否包含符号
        exclude_ambiguous: 排除歧义字符（Il1O0）
        memorable: 生成可读音节密码（便于口述/手输）
    """
    if error := _gate():
        return error
    try:
        if memorable:
            password = generator.generate_memorable(max(2, min(8, length // 5)))
        else:
            password = generator.generate_password(
                length, symbols=symbols, exclude_ambiguous=exclude_ambiguous)
        return json.dumps({
            "password": password,
            "strength": generator.assess_strength(password),
        }, ensure_ascii=False)
    except Exception as exc:
        return _map_error(exc, "生成密码")
