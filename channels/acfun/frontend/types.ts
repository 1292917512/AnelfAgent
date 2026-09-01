export interface AcfunLoginResult {
  success: boolean;
  error_msg?: string;
  need_captcha?: boolean;
  uid?: string;
  username?: string;
}

export interface AcfunLoginStatus {
  logined: boolean;
  uid: string;
  username: string;
  saved_at?: number | null;
  channel_running: boolean;
  online: boolean;
}

export interface AcfunLiveRoomStats {
  danmaku: number;
  likes: number;
  enters: number;
  follows: number;
  gifts: number;
  unknown_signals: number;
  reconnects: number;
  ticket_rotations: number;
  param_refreshes: number;
  last_error: string;
  last_signal_age?: number | null;
}

export interface AcfunLiveRoom {
  uid: string;
  state: "disconnected" | "connecting" | "connected" | "reconnecting" | "closed" | "stopped";
  detail: string;
  title: string;
  user_name: string;
  uptime: number;
  watching: string;
  likes: string;
  banana: string;
  danmaku_recent: number;
  recent_danmaku: AcfunLiveDanmakuItem[];
  recent_gifts: string[];
  stats: AcfunLiveRoomStats;
}

export interface AcfunLiveStatus {
  mode: boolean;
  watched: string[];
  rooms: AcfunLiveRoom[];
  state_events: string[];
  channel_running: boolean;
  logined: boolean;
}

export interface AcfunLiveDanmakuItem {
  uid: string;
  name: string;
  text: string;
}

export interface AcfunQrStartResult {
  session_id: string;
  qr_png: string;
  expire_seconds: number;
}

export interface AcfunQrStatusResult {
  status: "wait" | "scaned" | "confirmed" | "timeout" | "error";
  error?: string;
  success?: boolean;
  uid?: string;
  username?: string;
}

export interface AcfunCaptchaResult {
  image: string;
  key: string;
}
