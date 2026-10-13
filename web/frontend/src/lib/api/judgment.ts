/** 判断服务商与模型目录、状态和诊断；运行策略沿用统一配置元数据。 */

import { api } from "./client";
import type { DecisionProvider, JudgmentCatalog, JudgmentStatus, JudgmentTestResult } from "@/lib/types";

export const judgmentApi = {
  status: () => api.get<JudgmentStatus>("/judgment/status"),
  test: (model_id = "", inference = false) => api.post<JudgmentTestResult>("/judgment/test", { model_id, inference }),
  catalog: () => api.get<JudgmentCatalog>("/judgment/providers"),
  saveProvider: (id: string, provider: DecisionProvider) => api.put<JudgmentCatalog>(`/judgment/providers/${encodeURIComponent(id)}`, provider),
  deleteProvider: (id: string) => api.delete<JudgmentCatalog>(`/judgment/providers/${encodeURIComponent(id)}`),
  setDefault: (model_id: string) => api.put<JudgmentCatalog>("/judgment/default", { model_id }),
  setPriority: (model_ids: string[]) => api.put<JudgmentCatalog>("/judgment/priority", { model_ids }),
};
