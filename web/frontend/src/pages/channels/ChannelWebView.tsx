import { useState } from "react";
import { useTranslation } from "react-i18next";
import { cn } from "@/lib/utils";
import { ExternalLink } from "lucide-react";
import type { ConfigMeta } from "@/pages/channels/ConfigField";
import type { ConfigValues } from "@/lib/types";

export function ChannelWebView({
  channelKey,
  configs,
  values,
}: {
  channelKey: string;
  configs: Array<[string, ConfigMeta]>;
  values: ConfigValues;
}) {
  const { t } = useTranslation("channels");
  const [showIframe, setShowIframe] = useState(false);

  const webuiEntry = configs.find(([k]) =>
    k.endsWith(".napcat_webui_url") || k.endsWith(".webui_url") || k.endsWith(".dashboard_url")
  );
  if (!webuiEntry) return null;

  const url = String(values[webuiEntry[0]] || webuiEntry[1].value || webuiEntry[1].default || "");
  if (!url) return null;

  // 一律经本站同源代理访问频道 WebUI（外网可达）；
  // 携带配置 URL 的 query/hash（如 NapCat 的 ?token= 自动登录）
  // 注意：不能在早退 return 之后调用 hook，这里为纯字符串计算，无需 useMemo
  const proxyBase = `/api/channels/${channelKey}/webui/`;
  let proxyUrl = proxyBase;
  try {
    const u = new URL(url);
    proxyUrl = `${proxyBase}${u.search}${u.hash}`;
  } catch { /* URL 非法时退回基础代理路径 */ }

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <p className="text-xs font-semibold text-muted uppercase tracking-wider">
          {t("remotePanel")}
        </p>
        <div className="flex items-center gap-2">
          <a
            href={proxyUrl}
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
            src={proxyUrl}
            className="w-full border-0 bg-bg h-[50dvh] md:h-[600px]"
            sandbox="allow-same-origin allow-scripts allow-forms allow-popups"
            title={`${channelKey} WebUI`}
          />
        </div>
      )}
    </div>
  );
}
