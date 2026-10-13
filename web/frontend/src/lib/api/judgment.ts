/** 判断服务商与模型目录、状态和诊断；运行策略沿用统一配置元数据。 */

import { api } from "./client";
import type { DecisionProvider, JudgmentCatalog, JudgmentStatus, JudgmentTestResult } from "@/lib/types";

export const judgmentApi = {
  status: () => api.get<JudgmentStatus>("/judgment/status"),
  /** 固定样例经当前通道跑一次完整判断（三题型各一） */
  test: (model_id = "") => api.post<JudgmentTestResult>("/judgment/test", { model_id }),
  catalog: () => api.get<JudgmentCatalog>("/judgment/providers"),
  saveProvider: (id: string, provider: DecisionProvider) => api.put<JudgmentCatalog>(`/judgment/providers/${encodeURIComponent(id)}`, provider),
  deleteProvider: (id: string) => api.delete<JudgmentCatalog>(`/judgment/providers/${encodeURIComponent(id)}`),
  setDefault: (model_id: string) => api.put<JudgmentCatalog>("/judgment/default", { model_id }),
};
