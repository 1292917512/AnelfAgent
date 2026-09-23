/** 紧凑耗时格式化（Codex fmt_elapsed_compact 移植）：0s→59s→1m 05s→1h 02m。 */

export function formatElapsedCompact(ms: number): string {
  const totalSec = Math.floor(ms / 1000);
  if (totalSec < 60) return `${totalSec}s`;
  const min = Math.floor(totalSec / 60);
  const sec = totalSec % 60;
  if (min < 60) return `${min}m ${String(sec).padStart(2, "0")}s`;
  const hour = Math.floor(min / 60);
  const remMin = min % 60;
  return `${hour}h ${String(remMin).padStart(2, "0")}m`;
}

/** 压缩 token 计数（Codex format_tokens_compact 移植）：<1K 原样，1.2K/3.4M/5.6B。 */
export function formatTokensCompact(n: number): string {
  const units: Array<[number, string]> = [
    [1_000_000_000, "B"],
    [1_000_000, "M"],
    [1_000, "K"],
  ];
  for (const [value, suffix] of units) {
    if (n >= value) {
      const scaled = n / value;
      const text = scaled < 10 ? scaled.toFixed(1) : String(Math.round(scaled));
      return `${text}${suffix}`;
    }
  }
  return String(n);
}

/** 分级时间戳（Codex separators 移植）：今天只显示时分、今年省略年份、跨年完整。
 * 输入为 epoch 秒；输出为本地化紧凑字符串（避免每天翻历史都重复「2026-09-23」）。
 */
export function formatRelativeTimestamp(epochSec: number, now: Date = new Date()): string {
  const d = new Date(epochSec * 1000);
  const hh = String(d.getHours()).padStart(2, "0");
  const mm = String(d.getMinutes()).padStart(2, "0");
  const hm = `${hh}:${mm}`;

  const startOfDay = (x: Date) => new Date(x.getFullYear(), x.getMonth(), x.getDate());
  const sameDay = startOfDay(d).getTime() === startOfDay(now).getTime();
  if (sameDay) return hm;

  const sameYear = d.getFullYear() === now.getFullYear();
  const monthNames = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  const mon = monthNames[d.getMonth()];
  if (sameYear) return `${mon} ${d.getDate()} ${hm}`;
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")} ${hm}`;
}
