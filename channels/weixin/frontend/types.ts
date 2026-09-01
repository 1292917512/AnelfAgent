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
