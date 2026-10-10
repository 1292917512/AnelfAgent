/**
 * sticker 实体 API — 表情包与图片索引（/api/entity/sticker）。
 * 复用核心 axios 实例（认证与拦截器），面板与核心壳页面共用。
 */
import { api } from "@/lib/api";
import type {
  IndexedImageListResult,
  StickerItem,
  StickerListResult,
  StickerStats,
} from "./types";

export const stickersApi = {
  list: (params: { query?: string; page?: number; page_size?: number }) =>
    api.get<StickerListResult>("/entity/sticker", { params }),
  stats: () => api.get<StickerStats>("/entity/sticker/stats"),
  upload: (data: FormData) =>
    api.post<{ success: boolean; sticker: StickerItem }>("/entity/sticker", data, {
      headers: { "Content-Type": "multipart/form-data" },
      timeout: 120000,
    }),
  update: (id: string, data: { description?: string; tags?: string[]; emotion?: string }) =>
    api.put(`/entity/sticker/${encodeURIComponent(id)}`, data),
  reindex: (id: string) =>
    api.post(`/entity/sticker/${encodeURIComponent(id)}/reindex`, null, { timeout: 120000 }),
  rebuildEmbeddings: (mode: "mismatched" | "all" = "mismatched") =>
    api.post<{ ok: boolean; dims: number; cleared: Record<string, number> }>(
      "/entity/sticker/embedding/rebuild", { mode }, { timeout: 120000 }),
  remove: (id: string) => api.delete(`/entity/sticker/${encodeURIComponent(id)}`),
  fileUrl: (id: string) => `/api/entity/sticker/${encodeURIComponent(id)}/file`,
  listImages: (params: { page?: number; page_size?: number }) =>
    api.get<IndexedImageListResult>("/entity/sticker/images/list", { params }),
  imageFileUrl: (path: string) => `/api/entity/sticker/images/file?path=${encodeURIComponent(path)}`,
  removeImage: (path: string) =>
    api.delete("/entity/sticker/images", { params: { path } }),
};
