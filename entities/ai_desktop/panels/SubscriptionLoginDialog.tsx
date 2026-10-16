import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, Loader2, QrCode, RefreshCw } from "lucide-react";
import { Button, Modal } from "@/components/ui";
import { aiDesktopApi } from "./api";
import type { SubscriptionLogin } from "./types";

/** 一键获取官方二维码；凭据仅保存在服务端。 */
export function SubscriptionLoginDialog({ onClose }: { onClose: () => void }) {
  const { t } = useTranslation("ai_desktop");
  const client = useQueryClient();
  const [login, setLogin] = useState<SubscriptionLogin | null>(null);
  const [image, setImage] = useState("");
  const [failed, setFailed] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    setLogin(null);
    setImage("");
    setFailed(false);
    let stopped = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let imageUrl = "";
    let sessionId = "";
    const update = async () => {
      try {
        const { data } = await aiDesktopApi.loginStatus(sessionId);
        if (stopped) return;
        setFailed(false);
        setLogin(data);
        if (data.state === "complete") {
          await Promise.all([
            client.invalidateQueries({ queryKey: ["ai-desktop-modules"] }),
            client.invalidateQueries({ queryKey: ["ai-desktop-preview"] }),
          ]);
          return;
        }
        if (["waiting", "scanned"].includes(data.state) && !imageUrl) {
          try {
            const response = await aiDesktopApi.loginImage(data.id);
            if (stopped) return;
            imageUrl = URL.createObjectURL(response.data);
            setImage(imageUrl);
          } catch {
            // 扫码完成时二维码端点会先关闭，下一次状态查询确认结果。
          }
        }
        if (["starting", "waiting", "scanned", "confirming", "authorizing", "refreshing"].includes(data.state)) timer = setTimeout(() => void update(), 2000);
      } catch {
        if (!stopped) setFailed(true);
      }
    };
    void Promise.resolve().then(async () => {
      if (stopped) return;
      const { data } = await aiDesktopApi.startLogin();
      if (stopped) { void aiDesktopApi.cancelLogin(data.id).catch(() => {}); return; }
      sessionId = data.id;
      setLogin(data);
      void update();
    }).catch(() => { if (!stopped) setFailed(true); });
    return () => {
      stopped = true;
      clearTimeout(timer);
      if (imageUrl) URL.revokeObjectURL(imageUrl);
      if (sessionId) void aiDesktopApi.cancelLogin(sessionId).catch(() => {});
    };
  }, [client, attempt]);

  const confirm = async () => {
    if (!login || confirming) return;
    setConfirming(true);
    try {
      await aiDesktopApi.confirmLogin(login.id);
      setLogin((current) => current?.id === login.id ? { ...current, state: "authorizing" } : current);
    }
    catch { setFailed(true); }
    finally { setConfirming(false); }
  };
  const retry = async () => {
    if (login) await aiDesktopApi.cancelLogin(login.id).catch(() => {});
    setAttempt((value) => value + 1);
  };
  const state = failed ? "error" : login?.state ?? "starting";
  const busy = ["starting", "authorizing", "refreshing"].includes(state);
  const retryable = ["waiting", "expired", "cancelled", "error"].includes(state);
  return <Modal open onClose={onClose} title={t("subscription.login")} width="max-w-sm"
    footer={<>
      {retryable && <Button variant="secondary" onClick={() => void retry()}>
        <RefreshCw className="mr-1.5 h-4 w-4" />{t("subscription.refreshQr")}
      </Button>}
      <Button variant="secondary" onClick={onClose}>{t("subscription.closeLogin")}</Button>
    </>}>
    <div className="flex flex-col items-center gap-4 py-3 text-center">
      {["waiting", "scanned"].includes(state) && image
        ? <img src={image} alt={t("subscription.loginQr")} className="h-56 w-56 rounded-xl bg-white p-3" />
        : <div className="flex h-40 items-center justify-center">
          {busy ? <Loader2 className="h-9 w-9 animate-spin text-accent" />
            : state === "complete" ? <CheckCircle2 className="h-9 w-9 text-success" />
              : <QrCode className="h-9 w-9 text-muted" />}
        </div>}
      <p role="status" className="text-sm">{t(`subscription.loginStates.${state}`)}</p>
      {login?.error === "additional_verification" && <p className="text-xs text-muted">{t("subscription.additionalVerification")}</p>}
      {login?.error === "quota_refresh_failed" && <p className="text-xs text-muted">{t("subscription.quotaRefreshFailed")}</p>}
      {state === "waiting" && <p className="text-xs text-muted">{t("subscription.loginHint")}</p>}
      {state === "confirming" && <Button onClick={() => void confirm()} disabled={confirming}>
        {confirming && <Loader2 className="mr-1.5 h-4 w-4 animate-spin" />}{t("subscription.confirmLogin")}
      </Button>}
    </div>
  </Modal>;
}
