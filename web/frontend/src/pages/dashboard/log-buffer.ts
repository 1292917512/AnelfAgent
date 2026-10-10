import type { LogEntry } from "@/lib/types";

export const MAX_LOG_ENTRIES = 2000;
export const LOG_LEVELS = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] as const;

export function mergeLogs(current: LogEntry[], incoming: LogEntry[]): LogEntry[] {
  const entries = new Map(current.map((entry) => [entry.seq, entry]));
  incoming.forEach((entry) => entries.set(entry.seq, entry));
  return [...entries.values()].sort((a, b) => a.seq - b.seq).slice(-MAX_LOG_ENTRIES);
}
