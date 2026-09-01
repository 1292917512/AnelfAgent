import { api } from "@/lib/api";
import type {
  AcfunCaptchaResult,
  AcfunLiveStatus,
  AcfunLoginResult,
  AcfunLoginStatus,
  AcfunQrStartResult,
  AcfunQrStatusResult,
} from "./types";

export const acfunApi = {
  login: (payload: { username: string; password: string; key?: string; captcha?: string }) =>
    api.post<AcfunLoginResult>("/channels/acfun/login", payload),
  status: () => api.get<AcfunLoginStatus>("/channels/acfun/login/status"),
  logout: () => api.post("/channels/acfun/logout"),
  captcha: () => api.get<AcfunCaptchaResult>("/channels/acfun/login/captcha"),
  qrStart: () => api.post<AcfunQrStartResult>("/channels/acfun/qr/start"),
  qrStatus: (sessionId: string) =>
    api.get<AcfunQrStatusResult>(`/channels/acfun/qr/${encodeURIComponent(sessionId)}/status`),
  qrDiscard: (sessionId: string) =>
    api.delete(`/channels/acfun/qr/${encodeURIComponent(sessionId)}`),
  // 直播模式（实时状态轮询 + 控制）
  liveStatus: () => api.get<AcfunLiveStatus>("/channels/acfun/live/status"),
  liveMode: (enabled: boolean) =>
    api.post<{ success: boolean; live_mode: boolean; result: string }>(
      "/channels/acfun/live/mode", { enabled },
    ),
  liveWatch: (uid: string) =>
    api.post<{ success: boolean; result: string; watched?: string[]; error_msg?: string }>(
      "/channels/acfun/live/watch", { uid },
    ),
  liveUnwatch: (uid: string) =>
    api.post<{ success: boolean; result: string; watched?: string[]; error_msg?: string }>(
      "/channels/acfun/live/unwatch", { uid },
    ),
};
