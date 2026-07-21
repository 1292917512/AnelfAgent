import type { MCPServerConfig, MCPTransport } from "@/lib/types";

/** 键值对编辑行（env / headers 共用） */
export interface KVRow {
  k: string;
  v: string;
}

export interface EditingServer {
  name: string;
  config: MCPServerConfig;
}

export function inferTransport(config: MCPServerConfig): MCPTransport {
  if (config.transport) return config.transport;
  return config.command ? "stdio" : "streamable_http";
}

export function recordToRows(record?: Record<string, string>): KVRow[] {
  if (!record) return [];
  return Object.entries(record).map(([k, v]) => ({ k, v }));
}

export function rowsToRecord(rows: KVRow[]): Record<string, string> | undefined {
  const record: Record<string, string> = {};
  for (const row of rows) {
    const key = row.k.trim();
    if (key) record[key] = row.v;
  }
  return Object.keys(record).length > 0 ? record : undefined;
}

export function parseArgsText(text: string): string[] {
  return text
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);
}
