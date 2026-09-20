/** 聊天域 API — 认证 / 消息 / 子代理委托。 */

import { api } from "./client";
import type {
  ApiKeyCreated,
  ApiKeyInfo,
  AuthStatus,
  DelegationHistoryItem,
  DelegationOverviewItem,
  DelegationProgress,
  RunningDelegation,
} from "@/lib/types";
import i18n from "@/i18n";

export const authApi = {
  check: () => api.get<AuthStatus>("/auth/check"),
  login: (password: string) => api.post("/auth/login", { password }),
  logout: () => api.post("/auth/logout"),
  updatePassword: (newPassword: string) =>
    api.put("/auth/password", { new_password: newPassword }),
  listApiKeys: () => api.get<{ keys: ApiKeyInfo[] }>("/auth/api-keys"),
  createApiKey: (name = "default") =>
    api.post<ApiKeyCreated>("/auth/api-keys", { name }),
  rotateApiKey: (keyId: string) =>
    api.post<ApiKeyCreated>(`/auth/api-keys/${keyId}/rotate`),
  deleteApiKey: (keyId: string) =>
    api.delete<{ status: string }>(`/auth/api-keys/${keyId}`),
};

// ── 类型化 API 方法 ─────────────────────────────────────────────

// Chat

export const chatApi = {
  send: (message: string, userId = "web_user", userName?: string, files?: string[], chatId?: string) =>
    api.post("/chat/send", {
      message,
      user_id: userId,
      user_name: userName ?? i18n.t("user", { ns: "chat" }),
      ...(chatId ? { chat_id: chatId } : {}),
      ...(files?.length ? { files } : {}),
    }),
  upload: (file: File) => {
    const form = new FormData();
    form.append("file", file);
    return api.post("/chat/upload", form, {
      headers: { "Content-Type": "multipart/form-data" },
    });
  },
  history: (scopeId = "web_user", limit = 50, chatId?: string, beforeId?: number) =>
    api.get(`/chat/history`, {
      params: {
        scope_id: scopeId, limit,
        ...(chatId ? { chat_id: chatId } : {}),
        ...(beforeId ? { before_id: beforeId } : {}),
      },
    }),
  chats: (userId = "web_user") =>
    api.get<{ chats: Array<{ chat_id: string; scope_id: string; title: string; last_ts: number; message_count: number }> }>(
      "/chat/chats", { params: { user_id: userId } },
    ),
  botName: () => api.get<{ name: string }>("/chat/bot-name"),
  interrupt: (chatId?: string) =>
    api.post<{ status: string; interrupted?: boolean; cancelled_delegations?: number }>(
      "/chat/interrupt", { chat_id: chatId ?? "default" },
    ),
  delegations: (chatId?: string) =>
    api.get<{ running: RunningDelegation[] }>(
      "/chat/delegations", { params: { chat_id: chatId ?? "default" } },
    ),
  cancelDelegation: (delegationId: string) =>
    api.post<{ status: string; error?: string }>(`/chat/delegations/${delegationId}/cancel`),
  cancelPlan: (chatId: string, planId: string) =>
    api.post<{ status: string; error?: string }>("/chat/cancel-plan", { chat_id: chatId, plan_id: planId }),
};

// Delegations（全局子代理总览 — Dashboard「子代理」面板）

export const delegationApi = {
  overview: () => api.get<{ running: DelegationOverviewItem[] }>("/delegations/overview"),
  history: (limit = 20) =>
    api.get<{ items: DelegationHistoryItem[] }>("/delegations/history", { params: { limit } }),
  progress: (delegationId: string, tail = 200) =>
    api.get<DelegationProgress>(`/delegations/${delegationId}/progress`, { params: { tail } }),
  steer: (delegationId: string, message: string, mode: "steer" | "after" = "steer") =>
    api.post<{ status: string; error?: string; note?: string }>(
      `/delegations/${delegationId}/steer`, { message, mode },
    ),
  cancel: (delegationId: string) =>
    api.post<{ status: string; error?: string }>(`/delegations/${delegationId}/cancel`),
};

// Status
