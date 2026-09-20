/** 人脸面板共享工具：时间格式化与来源标注。 */

import type { SyntheticEvent } from "react";

export { formatNs, formatAgoNs } from "@/lib/utils";

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
