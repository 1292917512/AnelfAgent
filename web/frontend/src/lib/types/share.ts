/**
 * 聊天分享协议类型 — SSE share 事件载荷与消息契约（核心协议层）。
 * 管理面类型（ShareLink/ShareStats 等）在 entities/share/panels/types.ts。
 */

/** 分享类型：file=文件下载 / media=媒体渲染 / link=网址推送 */
export type ShareType = "file" | "media" | "link";

/** 媒体渲染种类（media 类型按扩展名检测） */
export type ShareMediaKind = "image" | "video" | "audio" | "pdf" | "html" | "";

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
