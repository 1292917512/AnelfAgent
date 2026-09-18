/** 人脸面板共享工具：时间格式化与来源标注。 */

import type { SyntheticEvent } from "react";

/** 纳秒时间戳 → 本地日期时间串（无效值返回占位符）。 */
export function formatNs(ns: number): string {
  if (!ns) return "-";
  const d = new Date(ns / 1_000_000);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ` +
    `${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

/** 纳秒时间戳 → 相对时间（如 3min / 2h / 1d）。 */
export function formatAgoNs(ns: number): string {
  if (!ns) return "-";
  const ago = Math.max(0, Math.round(Date.now() - ns / 1_000_000) / 1000);
  if (ago < 60) return `${Math.round(ago)}s`;
  if (ago < 3600) return `${Math.round(ago / 60)}min`;
  if (ago < 86400) return `${Math.round(ago / 3600)}h`;
  return `${Math.round(ago / 86400)}d`;
}

/** 文件名提取（去掉目录前缀）。 */
export function basename(path: string): string {
  if (!path) return "-";
  const idx = Math.max(path.lastIndexOf("/"), path.lastIndexOf("\\"));
  return idx >= 0 ? path.slice(idx + 1) : path;
}

/** 缩略图加载失败（源文件已清理等）时的内联占位图（灰底人像剪影）。 */
export const IMG_FALLBACK =
  "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 64 64'%3E" +
  "%3Crect width='64' height='64' fill='%231e2228'/%3E" +
  "%3Ccircle cx='32' cy='25' r='10' fill='%233a4048'/%3E" +
  "%3Crect x='14' y='39' width='36' height='17' rx='7' fill='%233a4048'/%3E%3C/svg%3E";

/** img onError 回退：源 404/401 时换占位图，避免浏览器破图标。 */
export function onImgError(e: SyntheticEvent<HTMLImageElement>): void {
  const el = e.currentTarget;
  el.onerror = null;
  el.src = IMG_FALLBACK;
}
