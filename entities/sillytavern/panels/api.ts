/**
 * 酒馆实体面板 API 封装（复用全局 axios 实例 api，认证/拦截器与主站一致）。
 * 后端端点全部在 /api/entity/sillytavern 前缀下。
 */
import { api, apiErrorMessage } from "@/lib/api";
import type {
  StCharacter,
  StCharacterCreatePayload,
  StChatMessagesResult,
  StChatSendResult,
  StConfig,
  StGitCheckoutResult,
  StGitCommitResult,
  StGitInfo,
  StGitUpdateResult,
  StGitVersionsResult,
  StLogsResult,
  StModelUpdatePayload,
  StModelUpdateResult,
  StMyModelsResult,
  StStatus,
  StUseMyModelResult,
} from "./types";

export { apiErrorMessage };

const BASE = "/entity/sillytavern";
// 启停/重启可能含首次 npm install 与 webpack 构建，最长 3 分钟
const LONG_TIMEOUT = 180_000;

export const sillytavernApi = {
  status: () => api.get<StStatus>(`${BASE}/status`),
  start: () => api.post<StStatus>(`${BASE}/start`, undefined, { timeout: LONG_TIMEOUT }),
  stop: () => api.post<StStatus>(`${BASE}/stop`, undefined, { timeout: LONG_TIMEOUT }),
  restart: () => api.post<StStatus>(`${BASE}/restart`, undefined, { timeout: LONG_TIMEOUT }),
  logs: (maxChars = 4000) => api.get<StLogsResult>(`${BASE}/logs`, { params: { max_chars: maxChars } }),

  getConfig: () => api.get<StConfig>(`${BASE}/config`),
  saveConfig: (data: Partial<StConfig>) => api.post<StConfig>(`${BASE}/config`, data),

  git: () => api.get<StGitInfo>(`${BASE}/git`),
  gitVersions: (remote = "origin") =>
    api.get<StGitVersionsResult>(`${BASE}/git/versions`, { params: { remote } }),
  gitUpdate: (remote: string, branch?: string) =>
    api.post<StGitUpdateResult>(`${BASE}/git/update`, { remote, branch: branch || null },
      { timeout: 300_000 }),
  gitCheckout: (name: string, remote = "origin") =>
    api.post<StGitCheckoutResult>(`${BASE}/git/checkout`, { name, remote },
      { timeout: 300_000 }),
  gitCommit: (message: string) =>
    api.post<StGitCommitResult>(`${BASE}/git/commit`, { message }, { timeout: 180_000 }),

  characters: () => api.get<{ count: number; characters: StCharacter[] }>(`${BASE}/characters`),
  character: (avatar: string) => api.get(`${BASE}/characters/${encodeURIComponent(avatar)}`),
  createCharacter: (data: StCharacterCreatePayload) =>
    api.post<{ ok: boolean; avatar: string }>(`${BASE}/characters/create`, data),
  editCharacter: (data: { avatar: string; field: string; value: string; current_name?: string }) =>
    api.post(`${BASE}/characters/edit`, data),
  deleteCharacter: (data: { avatar: string; delete_chats: boolean }) =>
    api.post(`${BASE}/characters/delete`, data),

  settings: () => api.get<Record<string, unknown>>(`${BASE}/settings`),
  saveModel: (data: StModelUpdatePayload) =>
    api.post<StModelUpdateResult>(`${BASE}/settings/model`, data),

  // AnelfAgent 模型直连酒馆
  myModels: () => api.get<StMyModelsResult>(`${BASE}/my-models`),
  useMyModel: (modelId: string) =>
    api.post<StUseMyModelResult>(`${BASE}/my-models/use`, { model_id: modelId }),

  // AI 与酒馆角色对话（走 anelf-bridge 插件）
  chatSend: (avatar: string, message: string, chatFile = "", name = "Anelf") =>
    api.post<StChatSendResult>(`${BASE}/chat/send`,
      { avatar, message, chat_file: chatFile, name }, { timeout: 120_000 }),
  chatContent: (avatar: string, fileName: string) =>
    api.get<StChatMessagesResult>(`${BASE}/chats/content`,
      { params: { avatar, file_name: fileName } }),
};

/** 酒馆网页内嵌地址（同源反代，经本站即可访问）。iframe src 走浏览器相对当前页，
 * 必须带 /api 前缀（axios baseURL 不影响 iframe 的地址解析）。 */
export const WEBUI_URL = `/api${BASE}/webui/`;
