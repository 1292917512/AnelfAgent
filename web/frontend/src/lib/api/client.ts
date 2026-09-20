/** API 客户端 — axios 实例、全局错误处理与共享错误工具。 */

import axios from "axios";

const api = axios.create({
  baseURL: "/api",
  timeout: 30000,
  headers: { "Content-Type": "application/json" },
});

// 模块前端插件（channels/*/frontend、entities/*/panel）经此实例复用认证与拦截器
export { api };

/** 可注入的全局 API 错误处理器（默认空实现；上层可接入 toast/日志） */
export type ApiErrorHandler = (err: unknown) => void;
let _apiErrorHandler: ApiErrorHandler | null = null;
export function setApiErrorHandler(handler: ApiErrorHandler | null): void {
  _apiErrorHandler = handler;
}

/** 非关键路径后台请求的统一失败日志（替代各处 .catch((e) => console.warn(...))） */
export function warnApiError(e: unknown): void {
  console.warn("[API]", e);
}

api.interceptors.response.use(
  (res) => res,
  (err) => {
    _apiErrorHandler?.(err);
    return Promise.reject(err);
  },
);

export default api;


export function apiErrorMessage(err: unknown, fallback: string): string {
  const axErr = err as { response?: { data?: { detail?: string } }; message?: string };
  return axErr?.response?.data?.detail || axErr?.message || fallback;
}

// Adapters（频道配置读写统一走 configMetaApi，组 adapter/<id>）
