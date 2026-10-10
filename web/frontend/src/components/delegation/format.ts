/** scope（user_{adapter}:{uid}）→ 频道 adapter 徽标文本 */
export function scopeAdapter(scope: string): string {
  const m = scope.match(/^[a-z]+_([^:]+):/);
  return m?.[1] ?? "";
}

export function formatDuration(totalSeconds: number): string {
  const s = Number.isFinite(totalSeconds) ? Math.max(0, Math.round(totalSeconds)) : 0;
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ${s % 60}s`;
  return `${Math.floor(m / 60)}h ${m % 60}m`;
}

export function formatTokens(n: number): string {
  if (n >= 1000) return `${(n / 1000).toFixed(1)}k`;
  return String(n);
}
