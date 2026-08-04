"""分享实体的数据模型。"""

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
    last_download_at: int     # Unix ms，0 表示未访问
    max_downloads: int        # 0 表示无限制
    status: str               # active | expired | revoked
    share_type: str = "file"  # file | media | link
    target_url: str = ""      # link 类型的目标网址
    media_kind: str = ""      # media 类型的渲染种类（image/video/audio/pdf/html）
    url: str = ""             # 主链接（file=下载 / media、link=预览页）
    download_url: str = ""    # 下载链接（media/file 有效）


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

    share_type: str = "file"  # file | media | link
    path: str = ""            # file/media 必填；link 忽略
    target_url: str = ""      # link 必填；file/media 忽略
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
