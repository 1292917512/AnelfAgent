import { api } from "@/lib/api";
import type {
  MediaConfig,
  MediaProvidersResult,
} from "./types";

// Media Library（媒体库实体专属路由 /api/entity/media）
export const mediaApi = {
  config: () => api.get<MediaConfig>("/entity/media/config"),
  updateConfig: (payload: Partial<MediaConfig>) =>
    api.put<MediaConfig>("/entity/media/config", payload),
  providers: () => api.get<MediaProvidersResult>("/entity/media/providers"),
};
