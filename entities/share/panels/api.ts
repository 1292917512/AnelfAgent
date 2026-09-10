/**
 * share 实体 API — 文件分享链接管理（/api/entity/share）。
 * 复用核心 axios 实例；聊天卡片渲染不经过本模块（SSE 载荷直渲）。
 */
import { api } from "@/lib/api";
import type {
  CreateShareRequest,
  DownloadLogListResult,
  ShareLink,
  ShareLinkListResult,
  ShareStats,
} from "./types";

export const shareApi = {
  list: (params: { status?: string; page?: number; page_size?: number; query?: string }) =>
    api.get<ShareLinkListResult>("/entity/share/links", { params }),
  create: (data: CreateShareRequest) =>
    api.post<ShareLink>("/entity/share/links", data),
  revoke: (token: string) =>
    api.delete(`/entity/share/links/${encodeURIComponent(token)}`),
  stats: () =>
    api.get<ShareStats>("/entity/share/stats"),
  getLogs: (params: { token?: string; page?: number; page_size?: number }) =>
    api.get<DownloadLogListResult>("/entity/share/logs", { params }),
};
