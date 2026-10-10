import { api } from "./client";
import type { SessionSummary, ThinkingSession } from "@/lib/types";

export const thinkingApi = {
  status: () => api.get<{ enabled: boolean }>("/thinking/status"),
  toggle: (enabled: boolean) => api.put<{ enabled: boolean }>("/thinking/toggle", { enabled }),
  sessions: (limit = 20) => api.get<{ sessions: SessionSummary[]; count: number }>("/thinking/sessions", { params: { limit } }),
  session: (id: string) => api.get<ThinkingSession>(`/thinking/sessions/${encodeURIComponent(id)}`),
};
