import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useQueryClient } from "@tanstack/react-query";
import { weixinQrApi } from "@/lib/api";
import { cn } from "@/lib/utils";
import { QrCode, X, RefreshCw, CheckCircle, Loader2, AlertCircle } from "lucide-react";

type Phase = "idle" | "loading" | "wait" | "scaned" | "confirmed" | "timeout" | "error";

/**
 * 微信扫码登录 — 点击按钮弹出二维码，扫码确认后凭据自动写入配置并启动频道。
 */
export function WeixinQrLogin({ compact = false }: { compact?: boolean }) {
  const { t } = useTranslation("channels");
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const [phase, setPhase] = useState<Phase>("idle");
  const [qrPng, setQrPng] = useState("");
  const [qrUrl, setQrUrl] = useState("");
  const [accountId, setAccountId] = useState("");
  const [error, setError] = useState("");
  const [refreshed, setRefreshed] = useState(false);
  const sessionRef = useRef<string | null>(null);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const pollingRef = useRef(false);
  const refreshTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const stopPolling = () => {
    if (timerRef.current) {
      clearInterval(timerRef.current);
      timerRef.current = null;
    }
  };

  const clearRefreshTimer = () => {
    if (refreshTimerRef.current) {
      clearTimeout(refreshTimerRef.current);
      refreshTimerRef.current = null;
    }
  };

  const discardSession = () => {
    stopPolling();
    clearRefreshTimer();
    const sid = sessionRef.current;
    sessionRef.current = null;
    if (sid) weixinQrApi.discard(sid).catch(() => undefined);
  };

  const start = async () => {
    discardSession();
    setPhase("loading");
    setError("");
    setAccountId("");
    setRefreshed(false);
    try {
      const { data } = await weixinQrApi.start();
      sessionRef.current = data.session_id;
      setQrPng(data.qr_png);
      setQrUrl(data.qr_url);
      setPhase("wait");
      timerRef.current = setInterval(poll, 1500);
    } catch (e) {
      setPhase("error");
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  const poll = async () => {
    const sid = sessionRef.current;
    if (!sid || pollingRef.current) return;
    pollingRef.current = true;
    try {
      const { data } = await weixinQrApi.status(sid);
      if (data.qr_png) {
        setQrPng(data.qr_png);
        setQrUrl(data.qr_url ?? "");
      }
      if (data.refreshed) {
        setRefreshed(true);
        clearRefreshTimer();
        refreshTimerRef.current = setTimeout(() => setRefreshed(false), 4000);
      }
      switch (data.status) {
        case "scaned":
          setPhase("scaned");
          break;
        case "confirmed":
          setPhase("confirmed");
          setAccountId(data.account_id ?? "");
          stopPolling();
          sessionRef.current = null;
          queryClient.invalidateQueries({ queryKey: ["adapters"] });
          queryClient.invalidateQueries({ queryKey: ["adapterConfigs"] });
          break;
        case "timeout":
          setPhase("timeout");
          setError(data.error ?? "");
          discardSession();
          break;
        case "error":
          setPhase("error");
          setError(data.error ?? "");
          discardSession();
          break;
        default:
          setPhase((p) => (p === "scaned" ? p : "wait"));
      }
    } catch {
      // 单次轮询失败静默，下一轮重试
    } finally {
      pollingRef.current = false;
    }
  };

  const close = () => {
    discardSession();
    setOpen(false);
    setPhase("idle");
  };

  useEffect(() => () => { discardSession(); clearRefreshTimer(); }, []);

  return (
    <>
      <button
        onClick={() => {
          setOpen(true);
          start();
        }}
        title={t("weixin.qrLogin")}
        className={cn(
          "flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-md border transition-all",
          "border-[rgba(34,197,94,0.3)] text-ok hover:bg-ok-subtle",
          compact && "px-2",
        )}
      >
        <QrCode size={14} />
        {t("weixin.qrLogin")}
      </button>

      {open && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/50"
          onClick={close}
        >
          <div
            className="w-[320px] rounded-lg border border-border bg-card p-5 space-y-4 shadow-xl"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-between">
              <h3 className="text-sm font-semibold text-heading">{t("weixin.qrTitle")}</h3>
              <button onClick={close} className="text-muted hover:text-foreground transition-colors">
                <X size={16} />
              </button>
            </div>

            <div className="flex flex-col items-center gap-3">
              <div className="w-[240px] h-[240px] rounded-md border border-border bg-white flex items-center justify-center overflow-hidden">
                {phase === "loading" ? (
                  <Loader2 size={28} className="animate-spin text-muted" />
                ) : qrPng ? (
                  <img src={qrPng} alt="WeChat QR" className="w-full h-full object-contain" />
                ) : (
                  <AlertCircle size={28} className="text-danger" />
                )}
              </div>

              {refreshed && (
                <p className="flex items-center gap-1 text-[11px] text-warn">
                  <RefreshCw size={12} /> {t("weixin.qrRefreshing")}
                </p>
              )}

              <div className="text-center space-y-1">
                {phase === "wait" && (
                  <p className="text-xs text-muted">{t("weixin.qrWaiting")}</p>
                )}
                {phase === "scaned" && (
                  <p className="text-xs text-warn">{t("weixin.qrScanned")}</p>
                )}
                {phase === "confirmed" && (
                  <p className="flex items-center justify-center gap-1 text-xs text-ok">
                    <CheckCircle size={14} />
                    {t("weixin.qrSuccess")}
                    {accountId && (
                      <span className="font-mono text-[10px] opacity-70">
                        {t("weixin.qrAccount")}: {accountId}
                      </span>
                    )}
                  </p>
                )}
                {(phase === "error" || phase === "timeout") && (
                  <p className="text-xs text-danger">
                    {phase === "timeout" ? t("weixin.qrTimeout") : t("weixin.qrError")}
                    {error ? `: ${error}` : ""}
                  </p>
                )}
                {(phase === "wait" || phase === "scaned" || phase === "loading") && (
                  <p className="text-[11px] text-muted">{t("weixin.qrHint")}</p>
                )}
                {qrUrl && phase !== "confirmed" && (
                  <p className="text-[10px] text-muted break-all font-mono opacity-60">{qrUrl}</p>
                )}
              </div>
            </div>

            <div className="flex justify-end gap-2">
              {(phase === "error" || phase === "timeout") && (
                <button
                  onClick={start}
                  className="flex items-center gap-1 px-3 py-1.5 text-xs font-medium rounded-md bg-accent text-white hover:opacity-90 transition-all"
                >
                  <RefreshCw size={12} /> {t("weixin.qrRetry")}
                </button>
              )}
              <button
                onClick={close}
                className="px-3 py-1.5 text-xs font-medium rounded-md border border-border text-muted hover:text-foreground hover:bg-hover transition-all"
              >
                {t("weixin.qrClose")}
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
