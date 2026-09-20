/** 工作区域 API — 文件浏览 / 全局搜索。 */

import { api } from "./client";
import type {
  GlobalSearchResult,
  WorkspaceFile,
  WorkspaceNode,
  WorkspaceRoot,
  WorkspaceSearchHit,
} from "@/lib/types";

export const workspaceApi = {
  tree: (path = "", depth = 2, root: WorkspaceRoot = "workspace") =>
    api.get<{ path: string; children: WorkspaceNode[]; truncated: boolean }>("/workspace/tree", { params: { path: path || undefined, depth, root } }),
  read: (path: string, root: WorkspaceRoot = "workspace") =>
    api.get<WorkspaceFile>("/workspace/file", { params: { path, root } }),
  write: (path: string, content: string, root: WorkspaceRoot = "workspace") =>
    api.put("/workspace/file", { path, content, root }),
  mkdir: (path: string, root: WorkspaceRoot = "workspace") => api.post("/workspace/mkdir", { path, root }),
  remove: (path: string, root: WorkspaceRoot = "workspace") => api.delete("/workspace/file", { params: { path, root } }),
  /** 重命名 / 移动（dst 为目标全路径，目标父目录须已存在） */
  move: (src: string, dst: string, root: WorkspaceRoot = "workspace") =>
    api.post<{ status: string; path: string }>("/workspace/move", { src, dst, root }),
  /** 上传文件到指定目录（multipart，重名 409） */
  upload: (dir: string, file: File, root: WorkspaceRoot = "workspace") => {
    const form = new FormData();
    form.append("file", file);
    return api.post<{ status: string; path: string; size: number }>("/workspace/upload", form, {
      params: { dir, root },
      headers: { "Content-Type": "multipart/form-data" },
    });
  },
  search: (q: string, limit = 30) =>
    api.get<{ query: string; files: WorkspaceSearchHit[] }>("/workspace/search", { params: { q, limit } }),
  /** 原始字节服务 URL（图片/音视频预览；inline 供 iframe 内联渲染，如 PDF） */
  rawUrl: (path: string, inline = false, root: WorkspaceRoot = "workspace") =>
    `/api/workspace/raw?path=${encodeURIComponent(path)}&root=${root}${inline ? "&inline=1" : ""}`,
};

/** 按文件名判断可预览的媒体类型 */

export const searchApi = {
  global: (q: string, limit = 10) =>
    api.get<GlobalSearchResult>("/search/global", { params: { q, limit } }),
};

// UI 交互（ui_ask 回答 / 工作台状态上报）
