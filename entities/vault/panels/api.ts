import { api } from "@/lib/api";
import type {
  VaultBreachReport,
  VaultEntry,
  VaultEntryCreate,
  VaultEntryList,
  VaultEntryUpdate,
  VaultGenerateResult,
  VaultImportResult,
  VaultStatus,
  VaultTotp,
} from "./types";

/** 从 axios 错误中提取后端 detail 文案。 */
export function vaultErrorMessage(err: unknown, fallback: string): string {
  const detail = (err as { response?: { data?: { detail?: string } } })?.response
    ?.data?.detail;
  return detail || fallback;
}

// 密码本（加密凭据库）
export const vaultApi = {
  status: () => api.get<VaultStatus>("/entity/vault/status"),
  setup: (mode: "machine" | "master", password: string = "") =>
    api.post<VaultStatus>("/entity/vault/setup", { mode, password }),
  unlock: (password: string = "") =>
    api.post<VaultStatus>("/entity/vault/unlock", { password }),
  lock: () => api.post<VaultStatus>("/entity/vault/lock"),
  changeMaster: (oldPassword: string, newPassword: string) =>
    api.post<VaultStatus>("/entity/vault/change-master", {
      old_password: oldPassword,
      new_password: newPassword,
    }),
  masterEnable: (password: string) =>
    api.post<VaultStatus>("/entity/vault/master/enable", { password }),
  masterDisable: () => api.post<VaultStatus>("/entity/vault/master/disable"),
  list: (params: { query?: string; tag?: string; favorite?: boolean; limit?: number }) =>
    api.get<VaultEntryList>("/entity/vault/entries", { params }),
  tags: () => api.get<string[]>("/entity/vault/entries/tags"),
  create: (data: VaultEntryCreate) =>
    api.post<VaultEntry>("/entity/vault/entries", data),
  update: (id: string, data: VaultEntryUpdate) =>
    api.put<VaultEntry>(`/entity/vault/entries/${encodeURIComponent(id)}`, data),
  remove: (id: string) =>
    api.delete(`/entity/vault/entries/${encodeURIComponent(id)}`),
  reveal: (id: string, field: string = "password") =>
    api.post<{ entry_id: string; field: string; value: string }>(
      `/entity/vault/entries/${encodeURIComponent(id)}/reveal`, { field }),
  totp: (id: string) =>
    api.get<VaultTotp>(`/entity/vault/entries/${encodeURIComponent(id)}/totp`),
  generate: (data: {
    length: number; symbols: boolean; exclude_ambiguous: boolean; memorable: boolean;
  }) => api.post<VaultGenerateResult>("/entity/vault/generate", data),
  importEntries: (data: {
    format: string; content: string; strategy: string; master_password?: string;
  }) => api.post<VaultImportResult>("/entity/vault/import", data),
  exportEntries: (format: string, masterPassword: string = "") =>
    api.post<{ format: string; content: string }>("/entity/vault/export", {
      format,
      master_password: masterPassword,
    }),
  breachCheck: () => api.post<VaultBreachReport>("/entity/vault/breach-check"),
};
