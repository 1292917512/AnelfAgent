/** 判断能力域 API — 通道状态与连通性测试（配置读写走 configMetaApi 统一配置面）。 */

import { api } from "./client";
import type { JudgmentStatus, JudgmentTestResult } from "@/lib/types";

export const judgmentApi = {
  status: () => api.get<JudgmentStatus>("/judgment/status"),
  /** 固定样例经当前通道跑一次完整判断（三题型各一） */
  test: () => api.post<JudgmentTestResult>("/judgment/test"),
};
