import { api } from "@/lib/api";

// 操作实体（桌面操控 + MCP 操作关联）
export const operationApi = {
  list: () => api.get("/entity/operation/list"),
  status: () => api.get("/entity/operation/status"),
  mcpTools: (server?: string) =>
    api.get("/entity/operation/mcp-tools", { params: { server: server || "" } }),
  registerMcp: (server: string, tool: string, note: string) =>
    api.post("/entity/operation/register-mcp", { server, tool, note }),
  update: (opId: string, payload: { note?: string; enabled?: boolean }) =>
    api.put(`/entity/operation/${opId}`, payload),
  remove: (opId: string) => api.delete(`/entity/operation/${opId}`),
  execute: (opId: string, args: Record<string, unknown>) =>
    api.post(`/entity/operation/${opId}/execute`, { args }),
};
