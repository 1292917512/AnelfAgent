import { useState } from "react";
import { useTranslation } from "react-i18next";
import { cn } from "@/lib/utils";
import { ExternalLink } from "lucide-react";
import type { ConfigMeta } from "@/pages/channels/ConfigField";

export function ChannelWebView({
  channelKey,
  configs,
  values,
}: {
  channelKey: string;
  configs: Array<[string, ConfigMeta]>;
  values: Record<string, unknown>;
}) {
  const { t } = useTranslation("channels");
  const [showIframe, setShowIframe] = useState(false);

  const webuiEntry = configs.find(([k]) =>
    k.endsWith(".napcat_webui_url") || k.endsWith(".webui_url") || k.endsWith(".dashboard_url")
  );
  if (!webuiEntry) return null;

  const url = String(values[webuiEntry[0]] || webuiEntry[1].value || webuiEntry[1].default || "");
  if (!url) return null;

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <p className="text-xs font-semibold text-muted uppercase tracking-wider">
          {t("remotePanel")}
        </p>
        <div className="flex items-center gap-2">
          <a
            href={url}
            target="_blank"
            rel="noopener noreferrer"
            className="flex items-center gap-1 px-2 py-1 text-[11px] text-muted rounded hover:bg-hover transition-colors"
          >
            <ExternalLink size={12} /> {t("openNewWindow")}
          </a>
          <button
            onClick={() => setShowIframe(!showIframe)}
            className={cn(
              "px-2 py-1 text-[11px] rounded transition-colors",
              showIframe
                ? "bg-accent text-white"
                : "text-muted hover:bg-hover",
            )}
          >
            {showIframe ? t("collapse") : t("inlinePreview")}
          </button>
        </div>
      </div>
      {showIframe && (
        <div className="rounded-md border border-border overflow-hidden">
          <iframe
            src={url}
            className="w-full border-0 bg-bg h-[50dvh] md:h-[600px]"
            sandbox="allow-same-origin allow-scripts allow-forms allow-popups"
            title={`${channelKey} WebUI`}
          />
        </div>
      )}
    </div>
  );
}
