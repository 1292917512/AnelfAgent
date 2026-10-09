import axios from "axios";

export const api = axios.create({
  baseURL: "/api", timeout: 30_000, headers: { "Content-Type": "application/json" },
});

export type ApiErrorHandler = (error: unknown) => void;
let onError: ApiErrorHandler | null = null;
let onUnauthorized: (() => void) | null = null;

export function setApiErrorHandler(handler: ApiErrorHandler | null): void { onError = handler; }
export function setUnauthorizedHandler(handler: (() => void) | null): void { onUnauthorized = handler; }
export function warnApiError(error: unknown): void { if (!axios.isCancel(error)) console.warn("[API]", error); }

api.interceptors.response.use((response) => response, (error: unknown) => {
  if (axios.isAxiosError(error) && error.response?.status === 401 && !error.config?.url?.startsWith("/auth/")) {
    onUnauthorized?.();
  }
  if (!axios.isCancel(error) && axios.isAxiosError(error) && !["get", "head"].includes(error.config?.method ?? "get")) onError?.(error);
  return Promise.reject(error);
});

function detailMessage(value: unknown): string | undefined {
  if (typeof value === "string" && value.trim()) return value;
  if (Array.isArray(value)) {
    const messages = value.map(detailMessage).filter((message): message is string => Boolean(message));
    return messages.length ? messages.join("; ") : undefined;
  }
  if (value && typeof value === "object") {
    if ("msg" in value) return detailMessage(value.msg);
    if ("message" in value) return detailMessage(value.message);
  }
  return undefined;
}

export function apiErrorMessage(error: unknown, fallback: string): string {
  if (axios.isAxiosError<unknown>(error)) {
    const data = error.response?.data;
    if (data && typeof data === "object") {
      if ("detail" in data) { const message = detailMessage(data.detail); if (message) return message; }
      if ("error" in data) { const message = detailMessage(data.error); if (message) return message; }
    }
  }
  return error instanceof Error && error.message ? error.message : fallback;
}

export default api;
