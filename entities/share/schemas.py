"""文件分享实体的数据模型。"""

from __future__ import annotations

from pydantic import BaseModel


class ShareLinkOut(BaseModel):
    """分享链接输出模型（API 响应 + 前端展示）。"""

    token: str
    file_path: str
    file_name: str
    file_size: int
    description: str
    expires_at: int           # Unix ms
    created_at: int           # Unix ms
    created_by: str
    download_count: int
    last_download_at: int     # Unix ms，0 表示未下载
    max_downloads: int        # 0 表示无限制
    status: str               # active | expired | revoked
    url: str = ""             # 完整下载 URL


class ShareLinkListResult(BaseModel):
    """分享链接分页列表。"""

    items: list[ShareLinkOut]
    total: int
    page: int
    page_size: int


class ShareStats(BaseModel):
    """分享统计总览。"""

    total: int
    active: int
    expired: int
    revoked: int
    total_downloads: int
    top_files: list[dict]     # [{file_path, file_name, count}]


class CreateShareRequest(BaseModel):
    """创建分享链接请求。"""

    path: str
    description: str = ""
    expires_in: str = "24h"   # 1h | 6h | 24h | 7d | 30d | never
    max_downloads: int = 0    # 0 表示无限制


class DownloadLogEntry(BaseModel):
    """下载审计日志条目。"""

    id: int
    token: str
    ip: str
    user_agent: str
    downloaded_at: int
    file_name: str
    file_size: int


class DownloadLogListResult(BaseModel):
    """下载审计日志分页列表。"""

    items: list[DownloadLogEntry]
    total: int
    page: int
    page_size: int
