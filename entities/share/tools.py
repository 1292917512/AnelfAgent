"""分享推送工具 — AI 可调用的分享链接管理。

3 个工具：
- create_share_link: 生成对外分享链接（文件下载 / 媒体渲染 / 网址推送）
- list_share_links: 列出当前所有分享链接（按状态/路径过滤）
- revoke_share_link: 撤销（关闭）一个分享链接

所有文件路径走 entities/filesystem/paths.py 的沙箱校验。
"""

from __future__ import annotations

import json
from typing import Any, Dict

from entities._sdk import ErrorCause, entity, error_from_exception, tool, tool_error

entity("share", "分享推送 - 文件下载 / 媒体渲染 / 网址推送，生成对外访问链接")

_CREATE_PROMPT = """生成对外分享链接，支持三种分享类型。

分享类型（share_type）:
- "file": 文件下载。对方打开链接直接下载文件。适合文档、压缩包等任意文件。
- "media": 媒体渲染。对方打开链接看到预览页：图片直接展示、视频带播放器、
  音频带控件、PDF/HTML 内嵌浏览。仅支持 图片/视频/音频/PDF/HTML 文件，
  其他扩展名会被拒绝（此时应改用 file）。
- "link": 网址推送。把一个已部署的网站（本地服务、外部站点均可）生成分享卡片，
  对方打开链接看到落地页（站点预览 + 直接访问按钮）。必须提供 target_url。

使用规则:
- file/media 的 path 必须是工作区沙箱内的文件（相对路径或绝对路径均可）。
- link 的 target_url 必须以 http:// 或 https:// 开头。
- description 用于记录分享用途，AI 应主动填写有意义的说明。
- expires_in 控制有效期，默认 24h；敏感内容建议缩短有效期。
- 相同内容重复分享会复用旧 token（避免链接泛滥）。
- 返回的 url 可直接发送给他人，对方无需登录即可访问。

重要纪律（必须遵守）:
- 只能将返回结果中的 url 字段【原样】发给用户，禁止编造/猜测域名，禁止自行拼接或"美化"链接。
- 返回结果含 error 字段时表示创建失败，必须如实告知用户失败原因，严禁谎称成功。
- 返回结果含 url_incomplete 时，说明系统未配置公网基址，url 只是相对路径、外部无法访问，
  必须提醒用户到 实体详情 → 配置 中填写 share_public_base_url。

参数:
- share_type: 分享类型，"file" / "media" / "link"，默认 "file"
- path: 工作区内的文件路径（file/media 必填）
- target_url: 目标网址（link 必填，如 http://127.0.0.1:8080）
- description: 分享说明（可选）
- expires_in: 有效期，可选值 "1h" / "6h" / "24h" / "7d" / "30d" / "never"，缺省跟随实体配置 share_default_expires_in
- max_downloads: 最大访问次数（0 无限制），负数跟随实体配置

返回: {"token", "url", "share_type", "expires_at", "file_name", ...}"""

_LIST_PROMPT = """列出当前所有分享链接，支持按状态和路径过滤。

使用规则:
- status 过滤链接状态：active=活跃中 / expired=已过期 / revoked=已撤销 / all=全部
- path_keyword 按文件路径模糊过滤（如 "uploads/" 匹配所有上传文件）
- 返回结果按创建时间倒序排列

参数:
- status: 状态过滤，默认 "active"
- path_keyword: 路径关键词过滤（可选）
- limit: 返回数量上限（默认 20，最大 100）

返回: {"items": [...], "total": N}，每个 item 包含
token/url/share_type/media_kind/file_name/file_size/expires_at/download_count/status
（url 为完整分享链接，只可原样转发，禁止改动）"""

_REVOKE_PROMPT = """撤销（关闭）一个分享链接，使其立即失效。

使用规则:
- 撤销后该 token 对应的分享将无法再被访问（返回 404）
- 撤销操作不可逆，但可以为同一内容重新创建新链接

参数:
- token: 要撤销的链接 token（必填）

返回: {"token", "status": "revoked"} 或 {"error": "..."}"""


def _attach_tool_urls(entry: Dict[str, Any], base_url: str) -> None:
    """按分享类型填充主链接 url 与 download_url（与 router 语义一致）。"""
    from .store import SHARE_TYPE_FILE, SHARE_TYPE_MEDIA, build_download_url, build_view_url

    share_type = entry.get("share_type", SHARE_TYPE_FILE)
    if share_type == SHARE_TYPE_FILE:
        entry["url"] = build_download_url(entry["token"], base_url)
        entry["download_url"] = entry["url"]
    else:
        entry["url"] = build_view_url(entry["token"], base_url)
        if share_type == SHARE_TYPE_MEDIA:
            entry["download_url"] = build_download_url(entry["token"], base_url)


async def _emit_share_created(entry: Dict[str, Any]) -> None:
    """发射分享创建事件 → 聊天 SSE → 前端分享卡片（失败不影响主流程）。"""
    try:
        from core.event_bus import EVENT_SHARE_CREATED, event_bus

        await event_bus.emit(EVENT_SHARE_CREATED, {
            "token": entry.get("token", ""),
            "url": entry.get("url", ""),
            "download_url": entry.get("download_url", ""),
            "share_type": entry.get("share_type", "file"),
            "media_kind": entry.get("media_kind", ""),
            "target_url": entry.get("target_url", ""),
            "file_name": entry.get("file_name", ""),
            "file_size": entry.get("file_size", 0),
            "description": entry.get("description", ""),
        })
    except Exception as e:
        from core.log import log
        log(f"分享事件发射失败: {e}", "DEBUG", tag="分享")


@tool(name="create_share_link", group="share", description=_CREATE_PROMPT)
async def create_share_link(
    path: str = "",
    share_type: str = "file",
    target_url: str = "",
    description: str = "",
    expires_in: str = "",
    max_downloads: int = -1,
) -> str:
    """生成对外分享链接（文件下载 / 媒体渲染 / 网址推送）。

    Args:
        path: 工作区内的文件路径（file/media 必填，相对或绝对）
        share_type: 分享类型（file=下载 / media=渲染 / link=网址推送）
        target_url: 目标网址（link 必填）
        description: 分享说明
        expires_in: 有效期（1h/6h/24h/7d/30d/never），空则跟随实体配置
        max_downloads: 最大访问次数（0 表示无限制），负数则跟随实体配置
    """
    from core.config import get_config, get_config_bool, get_config_int

    from .store import get_public_base_url, get_share_store

    try:
        if not get_config_bool("share_ai_auto_share", True):
            return tool_error("AI 自动分享已在实体配置中禁用",
                              cause=ErrorCause.STATE, retryable=False,
                              hint="如需使用，请在实体配置中开启 share_ai_auto_share")
        if not expires_in:
            expires_in = str(get_config("share_default_expires_in", "24h") or "24h")
        if max_downloads < 0:
            max_downloads = get_config_int("share_default_max_downloads", 0)

        store = get_share_store()
        entry = await store.create(
            file_path=path,
            target_url=target_url,
            share_type=share_type,
            description=description,
            expires_in=expires_in,
            created_by="agent",
            max_downloads=max_downloads,
        )
        base_url = get_public_base_url()
        _attach_tool_urls(entry, base_url)
        if not base_url.strip():
            entry["url_incomplete"] = True
            entry["warning"] = (
                "未配置公网基址 share_public_base_url，url 为相对路径，外部无法访问；"
                "请在实体详情的配置页填写公网基址（如 https://your-domain）"
            )
        await _emit_share_created(entry)
        return json.dumps(entry, ensure_ascii=False)
    except FileNotFoundError as e:
        return error_from_exception(e, action="创建分享链接")
    except ValueError as e:
        return error_from_exception(e, action="创建分享链接")
    except Exception as e:
        return error_from_exception(e, action="创建分享链接")


@tool(name="list_share_links", group="share", description=_LIST_PROMPT, concurrency_safe=True)
async def list_share_links(status: str = "active", path_keyword: str = "", limit: int = 20) -> str:
    """列出当前所有分享链接。

    Args:
        status: 状态过滤（active/expired/revoked/all）
        path_keyword: 路径关键词过滤
        limit: 返回数量上限
    """
    from .store import get_public_base_url, get_share_store

    try:
        store = get_share_store()
        result = await store.list(status=status, query=path_keyword, page_size=min(limit, 100))
        # 与 create_share_link 一致：直接给出最终可用的完整链接
        base_url = get_public_base_url()
        for item in result["items"]:
            _attach_tool_urls(item, base_url)
        return json.dumps(result, ensure_ascii=False)
    except Exception as e:
        return error_from_exception(e, action="查询分享链接")


@tool(name="revoke_share_link", group="share", description=_REVOKE_PROMPT)
async def revoke_share_link(token: str) -> str:
    """撤销一个分享链接。

    Args:
        token: 要撤销的链接 token
    """
    from .store import get_share_store

    try:
        store = get_share_store()
        entry = await store.revoke(token)
        if not entry:
            return tool_error("链接不存在或已失效", cause=ErrorCause.NOT_FOUND,
                              retryable=False, token=token)
        return json.dumps({"token": token, "status": "revoked"}, ensure_ascii=False)
    except Exception as e:
        return error_from_exception(e, action="撤销分享链接")
