import type { MCPServerConfig, MCPServerPatch, MCPTransport } from "@/lib/types";

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

export function rowsToRecord(rows: KVRow[]): Record<string, string> {
  const entries = new Map<string, string>();
  for (const row of rows) {
    const key = row.k.trim();
    if (entries.has(key)) throw new Error("form.duplicateKey");
    if (key) entries.set(key, row.v);
  }
  return Object.fromEntries(entries);
}

export function parseArgsText(text: string): string[] {
  return text
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);
}

export interface ServerDraft {
  name: string; transport: MCPTransport; url: string; command: string; argsText: string;
  envRows: KVRow[]; headerRows: KVRow[]; timeout: string; sseReadTimeout: string; callTimeout: string; enabled: boolean;
}

export function serverDraft(editing?: EditingServer | null): ServerDraft {
  const config = editing?.config ?? {};
  return { name: editing?.name ?? "", transport: inferTransport(config), url: config.url ?? "", command: config.command ?? "",
    argsText: (config.args ?? []).join("\n"), envRows: recordToRows(config.env), headerRows: recordToRows(config.headers),
    timeout: config.timeout == null ? "" : String(config.timeout), sseReadTimeout: config.sse_read_timeout == null ? "" : String(config.sse_read_timeout),
    callTimeout: config.call_timeout == null ? "" : String(config.call_timeout), enabled: config.enabled ?? true };
}

export function serverPatch(draft: ServerDraft, editing?: EditingServer | null): MCPServerPatch {
  const original = serverDraft(editing);
  const changed = (key: keyof ServerDraft) => !editing || JSON.stringify(draft[key]) !== JSON.stringify(original[key]);
  if (!draft.name.trim()) throw new Error("form.nameRequired");
  if (!(draft.transport === "stdio" ? draft.command : draft.url).trim()) throw new Error("form.urlOrCommandRequired");
  const patch: MCPServerPatch = {};
  if (changed("transport")) patch.transport = draft.transport;
  if (changed("enabled")) patch.enabled = draft.enabled;
  if (draft.transport === "stdio") {
    if (changed("command")) patch.command = draft.command.trim();
    if (changed("argsText")) patch.args = parseArgsText(draft.argsText);
    if (changed("envRows")) patch.env = rowsToRecord(draft.envRows);
    if (editing && changed("transport")) { patch.url = null; patch.headers = null; }
  } else {
    if (changed("url")) patch.url = draft.url.trim();
    if (changed("headerRows")) patch.headers = rowsToRecord(draft.headerRows);
    if (editing && changed("transport")) { patch.command = null; patch.args = null; patch.env = null; }
  }
  const timeouts = [["timeout", "timeout"], ["sseReadTimeout", "sse_read_timeout"], ["callTimeout", "call_timeout"]] as const;
  for (const [key, field] of timeouts) {
    if (key === "sseReadTimeout" && draft.transport === "stdio") continue;
    if (!changed(key)) continue;
    const text = draft[key].trim();
    if (!text) { if (editing) patch[field] = null; continue; }
    const value = Number(text);
    if (!Number.isFinite(value) || value <= 0) throw new Error("form.timeoutPositive");
    patch[field] = value;
  }
  return patch;
}
