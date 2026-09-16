/** 操作页共享类型（与 /api/operation 响应一一对应）。 */

export interface OperationParam {
  name: string;
  description?: string;
  type?: string;
  required?: boolean;
  enum?: string[];
}

export interface OperationEntry {
  id: string;
  kind: "desktop" | "mcp";
  title: string;
  description: string;
  annotation: string;
  enabled: boolean;
  server: string;
  tool: string;
  params: OperationParam[];
  removable: boolean;
}

export interface McpToolEntry {
  server: string;
  name: string;
  description: string;
  params: OperationParam[];
}

export interface McpServerStatus {
  name: string;
  connected: boolean;
  enabled?: boolean;
  tool_count?: number;
  last_error?: string;
}

export interface HistoryEntry {
  op: string;
  kind: string;
  args: string;
  ok: boolean;
  detail: string;
  ts: number;
}

export interface OperationStatus {
  desktop: { available: boolean; screen: number[] | null; hint: string };
  mcp: { available: boolean; servers: McpServerStatus[] };
  counts: { operations: number; enabled: number; mcp_registered: number };
  history: HistoryEntry[];
}

export function formatAgo(ts: number): string {
  const ago = Math.max(0, Math.round(Date.now() / 1000 - ts));
  if (ago < 60) return `${ago}s`;
  if (ago < 3600) return `${Math.round(ago / 60)}min`;
  return `${Math.round(ago / 3600)}h`;
}
