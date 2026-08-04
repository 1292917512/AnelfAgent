/** 分享类型：file=文件下载 / media=媒体渲染 / link=网址推送 */
export type ShareType = "file" | "media" | "link";

/** 媒体渲染种类（media 类型按扩展名检测） */
export type ShareMediaKind = "image" | "video" | "audio" | "pdf" | "html" | "";

export interface ShareLink {
  token: string;
  file_path: string;
  file_name: string;
  file_size: number;
  description: string;
  expires_at: number;
  created_at: number;
  created_by: string;
  download_count: number;
  last_download_at: number;
  max_downloads: number;
  status: "active" | "expired" | "revoked";
  share_type: ShareType;
  target_url: string;
  media_kind: ShareMediaKind;
  /** 主链接（file=下载 / media、link=预览页） */
  url: string;
  /** 下载链接（media/file 有效） */
  download_url: string;
}

export interface ShareLinkListResult {
  items: ShareLink[];
  total: number;
  page: number;
  page_size: number;
}

export interface ShareStats {
  total: number;
  active: number;
  expired: number;
  revoked: number;
  total_downloads: number;
  top_files: Array<{ file_path: string; file_name: string; count: number }>;
}

export interface CreateShareRequest {
  share_type?: ShareType;
  /** file/media 必填；link 忽略 */
  path?: string;
  /** link 必填；file/media 忽略 */
  target_url?: string;
  description?: string;
  expires_in?: string;
  max_downloads?: number;
}

export interface DownloadLogEntry {
  id: number;
  token: string;
  ip: string;
  user_agent: string;
  downloaded_at: number;
  file_name: string;
  file_size: number;
}

export interface DownloadLogListResult {
  items: DownloadLogEntry[];
  total: number;
  page: number;
  page_size: number;
}

/** 聊天内分享卡片信息（SSE share 事件载荷） */
export interface ChatShareInfo {
  token: string;
  url: string;
  download_url?: string;
  share_type: ShareType;
  media_kind: ShareMediaKind;
  target_url?: string;
  file_name: string;
  file_size?: number;
  description?: string;
}
