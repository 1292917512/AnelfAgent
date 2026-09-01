import { api } from "@/lib/api";
import type {
  NoneBotAdapterInfo,
  NoneBotConfig,
  NoneBotEnvStatus,
  NoneBotOpResult,
  NoneBotPackagesResult,
  NoneBotPluginsResult,
  NoneBotSourcesResult,
  NoneBotStatus,
  NoneBotStorePluginsResult,
} from "./types";

// NoneBot Bridge（完整客户端管理：worker / 环境 / 适配器 / 插件 / 商店 / 配置 / 日志）
export const nonebotApi = {
  status: () => api.get<NoneBotStatus>("/nonebot/status"),
  restart: () => api.post<NoneBotOpResult>("/nonebot/restart", null, { timeout: 90000 }),
  workerStart: () => api.post<NoneBotOpResult>("/nonebot/worker/start", null, { timeout: 120000 }),
  workerStop: () => api.post<NoneBotOpResult>("/nonebot/worker/stop", null, { timeout: 60000 }),
  adapters: () => api.get<{ adapters: NoneBotAdapterInfo[] }>("/nonebot/adapters"),
  installAdapter: (key: string, enable: boolean = true, source: string = "") =>
    api.post<NoneBotOpResult>("/nonebot/adapters/install", { key, enable, source }, { timeout: 300000 }),
  uninstallAdapter: (key: string) =>
    api.post<NoneBotOpResult>("/nonebot/adapters/uninstall", { key }, { timeout: 300000 }),
  enableAdapter: (key: string, enabled: boolean) =>
    api.post<NoneBotOpResult>("/nonebot/adapters/enable", { key, enabled }, { timeout: 60000 }),
  plugins: () => api.get<NoneBotPluginsResult>("/nonebot/plugins"),
  installPlugin: (moduleName: string, source: string = "", editable: boolean = false) =>
    api.post<NoneBotOpResult>(
      "/nonebot/plugins/install",
      { module_name: moduleName, source, editable },
      { timeout: 600000 },
    ),
  uninstallPlugin: (moduleName: string) =>
    api.post<NoneBotOpResult>("/nonebot/plugins/uninstall", { module_name: moduleName }, { timeout: 300000 }),
  enablePlugin: (module: string, enabled: boolean) =>
    api.post<NoneBotOpResult>("/nonebot/plugins/enable", { module, enabled }, { timeout: 60000 }),
  storePlugins: (query: string = "", limit: number = 60) =>
    api.get<NoneBotStorePluginsResult>("/nonebot/store/plugins", { params: { query, limit }, timeout: 60000 }),
  envStatus: () => api.get<NoneBotEnvStatus>("/nonebot/env"),
  envBootstrap: () => api.post<NoneBotOpResult>("/nonebot/env/bootstrap", null, { timeout: 600000 }),
  envUpgrade: (packages?: string[]) =>
    api.post<NoneBotOpResult>("/nonebot/env/upgrade", { packages: packages ?? null }, { timeout: 600000 }),
  envRebuild: () => api.post<NoneBotOpResult>("/nonebot/env/rebuild", null, { timeout: 900000 }),
  envPackages: () => api.get<NoneBotPackagesResult>("/nonebot/env/packages", { timeout: 60000 }),
  envSources: () => api.get<NoneBotSourcesResult>("/nonebot/env/sources"),
  envResync: () => api.post<NoneBotOpResult>("/nonebot/env/resync", null, { timeout: 900000 }),
  config: () => api.get<NoneBotConfig>("/nonebot/config"),
  saveConfig: (patch: Partial<NoneBotConfig>) => api.put<NoneBotOpResult>("/nonebot/config", patch),
  logs: (count: number = 200) => api.get<{ logs: string[] }>("/nonebot/logs", { params: { count } }),
  runCommand: (command: string, botId: string = "", adapter: string = "") =>
    api.post<{ ok: boolean; replies?: string[]; error?: string }>(
      "/nonebot/command", { command, bot_id: botId, adapter }, { timeout: 90000 },
    ),
};
