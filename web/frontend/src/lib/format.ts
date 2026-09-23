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
