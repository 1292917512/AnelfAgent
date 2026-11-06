import type { LogEntry } from "@/lib/types";

export const MAX_LOG_ENTRIES = 200;
export const LOG_LEVELS = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] as const;
export const DEFAULT_LOG_LEVELS = ["WARNING", "ERROR", "CRITICAL"] as const;

export function mergeLogs(current: LogEntry[], incoming: LogEntry[]): LogEntry[] {
  const entries = new Map(current.map((entry) => [entry.seq, entry]));
  incoming.forEach((entry) => entries.set(entry.seq, entry));
  return [...entries.values()].sort((a, b) => a.seq - b.seq).slice(-MAX_LOG_ENTRIES);
}

/** 仅合并同来源、同正文的告警；保留最高严重度和最近记录供日志定位。 */
export function groupAttentionLogs(logs: LogEntry[]): { log: LogEntry; count: number; danger: boolean }[] {
  const groups = new Map<string, { log: LogEntry; count: number; danger: boolean }>();
  const seen = new Set<number>();
  for (const log of [...logs].sort((a, b) => b.timestamp - a.timestamp || b.seq - a.seq)) {
    if (seen.has(log.seq)) continue;
    seen.add(log.seq);
    const key = JSON.stringify([log.tag, log.message]);
    const danger = log.level === "ERROR" || log.level === "CRITICAL";
    const group = groups.get(key);
    if (group) { group.count++; group.danger ||= danger; }
    else groups.set(key, { log, count: 1, danger });
  }
  return [...groups.values()];
}
