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
  url: string;
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
  path: string;
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
