/** 工作区文件类型判断（预览/媒体分类，纯前端展示逻辑）。 */

import type { WorkspaceFileKind } from "./types";

export function workspaceMediaKind(name: string): "image" | "video" | "audio" | null {
  const ext = name.split(".").pop()?.toLowerCase() || "";
  if (["jpg", "jpeg", "png", "gif", "webp", "bmp", "svg", "ico"].includes(ext)) return "image";
  if (["mp4", "webm", "mov", "mkv", "avi", "flv"].includes(ext)) return "video";
  if (["mp3", "wav", "ogg", "flac", "m4a", "opus"].includes(ext)) return "audio";
  return null;
}

/** 可按富格式预览的文件类型（按扩展名分类） */

export function workspaceFileKind(name: string): WorkspaceFileKind | null {
  const ext = name.split(".").pop()?.toLowerCase() || "";
  if (["md", "markdown"].includes(ext)) return "markdown";
  if (["html", "htm"].includes(ext)) return "html";
  if (["csv", "tsv"].includes(ext)) return "csv";
  if (ext === "pdf") return "pdf";
  if (ext === "docx") return "docx";
  if (["xlsx", "xls"].includes(ext)) return "xlsx";
  return null;
}

/** 是否为可预览的二进制文档（pdf/docx/xlsx，媒体类型由 workspaceMediaKind 覆盖） */

export function isPreviewableBinary(name: string): boolean {
  const kind = workspaceFileKind(name);
  return kind === "pdf" || kind === "docx" || kind === "xlsx";
}

/** 视频文件的浏览器播放支持级别：native 原生可播 / flv 经 mpegts.js 可播 / unsupported 无法在线播放 */

export function workspaceVideoSupport(name: string): "native" | "flv" | "unsupported" | null {
  const ext = name.split(".").pop()?.toLowerCase() || "";
  if (["mp4", "webm", "mov"].includes(ext)) return "native";
  if (ext === "flv") return "flv";
  if (["mkv", "avi"].includes(ext)) return "unsupported";
  return null;
}

// 全局搜索
