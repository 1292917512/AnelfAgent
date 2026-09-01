import { api } from "@/lib/api";
import type {
  SshConnection,
  SshConnectionCreateRequest,
  SshConnectionListResult,
  SshConnectionUpdateRequest,
  SshExecRequest,
  SshExecResult,
} from "./types";

// SSH（远程管理）
export const sshApi = {
  list: () =>
    api.get<SshConnectionListResult>("/entity/ssh/connections"),
  create: (data: SshConnectionCreateRequest) =>
    api.post<SshConnection>("/entity/ssh/connections", data),
  update: (name: string, data: SshConnectionUpdateRequest) =>
    api.put<SshConnection>(`/entity/ssh/connections/${encodeURIComponent(name)}`, data),
  remove: (name: string) =>
    api.delete(`/entity/ssh/connections/${encodeURIComponent(name)}`),
  connect: (name: string) =>
    api.post<SshConnection>(`/entity/ssh/connections/${encodeURIComponent(name)}/connect`),
  disconnect: (name: string) =>
    api.post<SshConnection>(`/entity/ssh/connections/${encodeURIComponent(name)}/disconnect`),
  setDefault: (name: string) =>
    api.post("/entity/ssh/default", { name }),
  exec: (name: string, data: SshExecRequest) =>
    api.post<SshExecResult>(`/entity/ssh/connections/${encodeURIComponent(name)}/exec`, data),
};
