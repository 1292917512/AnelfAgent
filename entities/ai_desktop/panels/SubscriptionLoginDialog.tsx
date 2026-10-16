import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { useQueryClient } from "@tanstack/react-query";
import { ZoomIn, ZoomOut } from "lucide-react";
import { Button, Modal } from "@/components/ui";
import { aiDesktopApi } from "./api";
import type { SubscriptionLogin } from "./types";

/** 按需呈现官方扫码授权页；关闭后取消会话，凭据不返回浏览器。 */
export function SubscriptionLoginDialog({ onClose }: { onClose: () => void }) {
  const { t } = useTranslation("ai_desktop");
  const client = useQueryClient();
  const [login, setLogin] = useState<SubscriptionLogin | null>(null);
  const [image, setImage] = useState("");
  const [failed, setFailed] = useState(false);
  const [clicking, setClicking] = useState(false);
  const [zoom, setZoom] = useState(false);

  useEffect(() => {
    let stopped = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let imageUrl = "";
    let sessionId = "";
    const update = async () => {
      try {
        const { data } = await aiDesktopApi.loginStatus(sessionId);
        if (stopped) return;
        setLogin(data);
        if (data.state === "complete") {
          await Promise.all([
            client.invalidateQueries({ queryKey: ["ai-desktop-modules"] }),
            client.invalidateQueries({ queryKey: ["ai-desktop-preview"] }),
          ]);
          return;
        }
        if (data.state === "waiting") {
          try {
            const response = await aiDesktopApi.loginImage(data.id);
            if (stopped) return;
            if (imageUrl) URL.revokeObjectURL(imageUrl);
            imageUrl = URL.createObjectURL(response.data);
            setImage(imageUrl);
          } catch {
            // 完成授权时截图端点会先关闭，下一次状态查询确认结果。
          }
        }
        if (["starting", "waiting", "refreshing"].includes(data.state)) timer = setTimeout(() => void update(), 2000);
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
  }, [client]);

  const click = async (event: React.MouseEvent<HTMLButtonElement>) => {
    if (!login || clicking) return;
    const bounds = event.currentTarget.getBoundingClientRect();
    setClicking(true);
    try {
      await aiDesktopApi.loginClick(login.id,
        Math.min(login.width - 1, Math.max(0, Math.floor((event.clientX - bounds.left) / bounds.width * login.width))),
        Math.min(login.height - 1, Math.max(0, Math.floor((event.clientY - bounds.top) / bounds.height * login.height))));
    } catch { setFailed(true); }
    finally { setClicking(false); }
  };
  const unavailable = login?.error === "browser_not_installed";
  return <Modal open onClose={onClose} title={t("subscription.login")} width="max-w-4xl"
    footer={<Button variant="secondary" onClick={onClose}>{t("subscription.closeLogin")}</Button>}>
    <div className="space-y-3">
      <p className="text-sm text-muted">{t("subscription.loginHint")}</p>
      <p role="status" className="text-sm">{t(`subscription.loginStates.${failed ? "error" : unavailable ? "unavailable" : login?.state ?? "starting"}`)}</p>
      {unavailable && <pre className="whitespace-pre-wrap break-all rounded bg-elevated p-3 text-xs">{"uv sync --extra subscription-login\nuv run --extra subscription-login playwright install chromium"}</pre>}
      {!failed && image && login?.state === "waiting" && <>
        <Button size="sm" variant="secondary" onClick={() => setZoom(!zoom)} aria-pressed={zoom}>
          {zoom ? <ZoomOut className="mr-1 h-3.5 w-3.5" /> : <ZoomIn className="mr-1 h-3.5 w-3.5" />}
          {t(zoom ? "subscription.fitLogin" : "subscription.zoomLogin")}
        </Button>
        <div className="max-h-[60vh] overflow-auto rounded-lg border border-border bg-white">
          <button type="button" onClick={(event) => void click(event)} disabled={clicking}
            aria-label={t("subscription.loginPage")} className="block" style={{ width: zoom ? login.width : "100%" }}>
            <img src={image} alt={t("subscription.loginPage")} className="block h-auto w-full" />
          </button>
        </div>
      </>}
    </div>
  </Modal>;
}
