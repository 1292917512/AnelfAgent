/**
 * devops 实体 API — 服务重启/构建/git 更新/崩溃信息（/api/entity/devops）。
 * 复用核心 axios 实例，与 AI 运维工具共用同一后端实现。
 */
import { api } from "@/lib/api";
import type {
  DevopsActionResult,
  DevopsBuildState,
  DevopsCrashInfo,
} from "./types";

export const devopsApi = {
  restart: () => api.post<DevopsActionResult>("/entity/devops/restart"),
  crashInfo: () => api.get<DevopsCrashInfo>("/entity/devops/crash-info"),
  buildAndRestart: () => api.post<DevopsActionResult>("/entity/devops/build-restart"),
  buildState: () => api.get<DevopsBuildState>("/entity/devops/build-state"),
  update: () => api.post<DevopsActionResult>("/entity/devops/update"),
  updateAndRestart: () => api.post<DevopsActionResult>("/entity/devops/update-restart"),
};
