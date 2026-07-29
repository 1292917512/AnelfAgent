"""文件分享工具 — AI 可调用的分享链接管理。

3 个工具：
- create_share_link: 为工作区文件生成对外可下载链接
- list_share_links: 列出当前所有分享链接（按状态/路径过滤）
- revoke_share_link: 撤销（关闭）一个分享链接

所有路径走 entities/filesystem/paths.py 的沙箱校验。
"""

from __future__ import annotations

import json
from typing import Any, Dict

from entities._sdk import entity, tool

entity("file_share", "文件分享 - 将工作区文件生成为外部可下载链接")

_CREATE_PROMPT = """为工作区内的文件生成分享链接（对外可下载）。

使用规则:
- path 必须是工作区沙箱内的文件（相对路径或绝对路径均可）。
- description 用于记录分享用途，AI 应主动填写有意义的说明。
- expires_in 控制有效期，默认 24h；敏感文件建议缩短有效期。
- 相同文件重复分享会复用旧 token（避免链接泛滥）。
- 返回的 url 可直接发送给他人，对方无需登录即可下载。

参数:
- path: 工作区内的文件路径（必填）
- description: 分享说明（可选）
- expires_in: 有效期，可选值 "1h" / "6h" / "24h" / "7d" / "30d" / "never"，默认 "24h"

返回: {"token", "url", "expires_at", "file_name", "file_size"}"""

_LIST_PROMPT = """列出当前所有分享链接，支持按状态和路径过滤。

使用规则:
- status 过滤链接状态：active=活跃中 / expired=已过期 / revoked=已撤销 / all=全部
- path_keyword 按文件路径模糊过滤（如 "uploads/" 匹配所有上传文件）
- 返回结果按创建时间倒序排列

参数:
- status: 状态过滤，默认 "active"
- path_keyword: 路径关键词过滤（可选）
- limit: 返回数量上限（默认 20，最大 100）

返回: {"items": [...], "total": N}，每个 item 包含 token/file_name/file_size/expires_at/download_count/status"""

_REVOKE_PROMPT = """撤销（关闭）一个分享链接，使其立即失效。

使用规则:
- 撤销后该 token 对应的文件将无法再被下载（返回 404）
- 撤销操作不可逆，但可以为同一文件重新创建新链接

参数:
- token: 要撤销的链接 token（必填）

返回: {"token", "status": "revoked"} 或 {"error": "..."}"""


@tool(name="create_share_link", group="file_share", description=_CREATE_PROMPT)
async def create_share_link(path: str, description: str = "", expires_in: str = "24h", max_downloads: int = 0) -> str:
    """为工作区文件生成对外可下载的分享链接。

    Args:
        path: 工作区内的文件路径（相对或绝对）
        description: 分享说明
        expires_in: 有效期（1h/6h/24h/7d/30d/never）
        max_downloads: 最大下载次数（0 表示无限制）
    """
    from .store import get_share_store

    try:
        store = get_share_store()
        entry = await store.create(
            file_path=path,
            description=description,
            expires_in=expires_in,
            created_by="agent",
            max_downloads=max_downloads,
        )
        # URL 由 router.py 在 HTTP 层拼接（需要 Request 对象），这里返回相对路径模板
        entry["url"] = f"/api/entity/share/d/{entry['token']}"
        return json.dumps(entry, ensure_ascii=False)
    except FileNotFoundError as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)
    except ValueError as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": f"创建分享链接失败: {e}"}, ensure_ascii=False)


@tool(name="list_share_links", group="file_share", description=_LIST_PROMPT, concurrency_safe=True)
async def list_share_links(status: str = "active", path_keyword: str = "", limit: int = 20) -> str:
    """列出当前所有分享链接。

    Args:
        status: 状态过滤（active/expired/revoked/all）
        path_keyword: 路径关键词过滤
        limit: 返回数量上限
    """
    from .store import get_share_store

    try:
        store = get_share_store()
        result = await store.list(status=status, query=path_keyword, page_size=min(limit, 100))
        return json.dumps(result, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": f"查询分享链接失败: {e}"}, ensure_ascii=False)


@tool(name="revoke_share_link", group="file_share", description=_REVOKE_PROMPT)
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
            return json.dumps({"error": "链接不存在或已失效"}, ensure_ascii=False)
        return json.dumps({"token": token, "status": "revoked"}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": f"撤销分享链接失败: {e}"}, ensure_ascii=False)
