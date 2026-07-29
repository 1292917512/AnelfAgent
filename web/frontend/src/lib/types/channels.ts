export interface AdapterInfo {
  key: string;
  name: string;
  status: string;
  status_display: string;
  detail?: string;
  ws_mode?: string;
  ws_connected?: boolean;
  online?: boolean;
  self_id?: string;
  capabilities: string[];
}

export interface AdapterListResult {
  ready: boolean;
  adapters: AdapterInfo[];
}

export interface ChannelSelfInfo {
  user_id: string;
  user_name: string;
  platform: string;
}

export interface ChannelTestHealthResult {
  ready: boolean;
  running?: boolean;
  status?: string;
  detail?: string;
  capabilities?: string[];
  healthy?: boolean;
  health_detail?: string;
  latency_ms?: number | null;
  last_error?: string | null;
  self_info?: ChannelSelfInfo;
  error?: string;
}

export interface ChannelTestSendResult {
  ready: boolean;
  success: boolean;
  chat_id?: string;
  message_id?: string | null;
  error?: string;
}

export interface ChannelToolParam {
  name: string;
  type: string;
  required: boolean;
  description: string;
}

export interface ChannelToolInfo {
  name: string;
  description: string;
  /** 是否跨频道共享的公共能力接口（开关仅按频道生效） */
  common: boolean;
  sensitive: boolean;
  enabled: boolean;
  globally_enabled: boolean;
  supporting_channels?: string[];
  params: ChannelToolParam[];
}

export interface ChannelToolsResult {
  ready: boolean;
  running?: boolean;
  tools: ChannelToolInfo[];
}

export interface ChannelToolToggleResult {
  name: string;
  enabled: boolean;
  common: boolean;
}

export interface ChannelToolTestResult {
  ready: boolean;
  success: boolean;
  result?: string;
  latency_ms?: number;
  error?: string;
}

// ── Weixin QR Login ────────────────────────────────────────────

export interface WeixinQrStartResult {
  session_id: string;
  qr_png: string;
  qr_url: string;
}

export interface WeixinQrStatusResult {
  status: "wait" | "scaned" | "confirmed" | "timeout" | "error";
  qr_png?: string;
  qr_url?: string;
  refreshed?: boolean;
  account_id?: string;
  error?: string;
}
