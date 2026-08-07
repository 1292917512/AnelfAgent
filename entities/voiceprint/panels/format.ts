/** 面板共享工具：时间/时长格式化。 */

/** 纳秒时间戳 → 本地日期时间串（无效值返回占位符）。 */
export function formatNs(ns: number): string {
  if (!ns) return "-";
  const d = new Date(ns / 1_000_000);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ` +
    `${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

/** 毫秒时长 → 人类可读（如 3.2h / 45m / 30s）。 */
export function formatDuration(ms: number): string {
  if (ms >= 3_600_000) return `${(ms / 3_600_000).toFixed(1)}h`;
  if (ms >= 60_000) return `${Math.round(ms / 60_000)}m`;
  return `${Math.round(ms / 1000)}s`;
}

/** 文件内毫秒偏移 → mm:ss。 */
export function formatOffset(ms: number): string {
  const total = Math.floor(ms / 1000);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${pad(Math.floor(total / 60))}:${pad(total % 60)}`;
}
