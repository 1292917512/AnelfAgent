import { api } from "@/lib/api";
import type { WeixinQrStartResult, WeixinQrStatusResult } from "./types";

// Weixin QR Login（微信扫码登录）
export const weixinQrApi = {
  start: () => api.post<WeixinQrStartResult>("/channels/weixin/qr/start"),
  status: (sessionId: string) =>
    api.get<WeixinQrStatusResult>(`/channels/weixin/qr/${encodeURIComponent(sessionId)}/status`),
  discard: (sessionId: string) =>
    api.delete(`/channels/weixin/qr/${encodeURIComponent(sessionId)}`),
};
