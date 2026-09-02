/**
 * 酒馆网页内嵌标签页 — 通过实体反代（/api/entity/sillytavern/webui）同源嵌入，
 * 外网访问本站即可打开只监听回环的酒馆页面。
 */
import { useQuery } from "@tanstack/react-query";
import { ExternalLink, MonitorPlay, RefreshCw } from "lucide-react";
import { useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { LoadingBlock } from "@/components/ui";
import { Card } from "@/components/common/Card";
import { WEBUI_URL, sillytavernApi } from "./api";

export function WebPanel() {
  const { t } = useTranslation(["sillytavern"]);
  const iframeRef = useRef<HTMLIFrameElement>(null);
  const [loaded, setLoaded] = useState(false);
  const [reloadKey, setReloadKey] = useState(0);

  const { data: status } = useQuery({
    queryKey: ["sillytavern", "status"],
    queryFn: () => sillytavernApi.status().then((r) => r.data),
    refetchInterval: 5000,
  });

  const running = Boolean(status?.running);

  const handleReload = () => {
    setLoaded(false);
    setReloadKey((k) => k + 1);
  };

  if (!running) {
    return (
      <Card className="flex flex-col items-center justify-center gap-3 py-20 text-muted">
        <MonitorPlay size={40} className="opacity-40" />
        <p className="text-sm">{t("sillytavern:common.notRunning")}</p>
      </Card>
    );
  }

  return (
    <div className="flex h-full min-h-[60vh] flex-col gap-2">
      <div className="flex items-center justify-between">
        <span className="text-xs text-muted">{WEBUI_URL}</span>
        <div className="flex gap-2">
          <button
            type="button"
            onClick={handleReload}
            className="flex items-center gap-1.5 rounded-md border border-border bg-panel px-2.5 py-1.5 text-xs text-foreground hover:bg-card"
          >
            <RefreshCw size={13} /> {t("sillytavern:common.refresh")}
          </button>
          <a
            href={WEBUI_URL}
            target="_blank"
            rel="noreferrer"
            className="flex items-center gap-1.5 rounded-md border border-border bg-panel px-2.5 py-1.5 text-xs text-foreground hover:bg-card"
          >
            <ExternalLink size={13} /> {t("sillytavern:web.newWindow")}
          </a>
        </div>
      </div>
      <Card className="relative flex-1 overflow-hidden p-0">
        {!loaded && (
          <div className="absolute inset-0 flex items-center justify-center">
            <LoadingBlock />
          </div>
        )}
        <iframe
          key={reloadKey}
          ref={iframeRef}
          src={WEBUI_URL}
          title="SillyTavern"
          onLoad={() => setLoaded(true)}
          className="h-full w-full border-0 bg-white"
          allow="clipboard-read; clipboard-write"
        />
      </Card>
    </div>
  );
}
