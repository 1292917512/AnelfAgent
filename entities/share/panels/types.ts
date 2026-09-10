/** share 实体管理面类型（/api/entity/share 链接/统计/日志）。 */
import type { ShareMediaKind, ShareType } from "@/lib/types/share";

export type { ShareMediaKind, ShareType };

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
