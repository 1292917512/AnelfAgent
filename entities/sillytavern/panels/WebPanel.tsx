/**
 * 酒馆网页入口 — 直接链接到本机酒馆（127.0.0.1:<port>）。
 *
 * 不做 iframe 内嵌：酒馆响应头带 X-Frame-Options: SAMEORIGIN，跨端口
 * iframe 会被浏览器拒载白屏；且跨源 cookie/storage 隔离会让酒馆前端状态异常。
 * 直达新标签页最简单可靠。
 */
import { useQuery } from "@tanstack/react-query";
import { ExternalLink, MonitorPlay } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Button, LoadingBlock } from "@/components/ui";
import { Card } from "@/components/common/Card";
import { sillytavernApi } from "./api";

export function WebPanel() {
  const { t } = useTranslation(["sillytavern"]);

  const { data: status, isLoading } = useQuery({
    queryKey: ["sillytavern", "status"],
    queryFn: () => sillytavernApi.status().then((r) => r.data),
    refetchInterval: 5000,
  });

  if (isLoading) return <LoadingBlock />;

  const running = Boolean(status?.running);

  if (!running) {
    return (
      <Card className="flex flex-col items-center justify-center gap-3 py-20 text-muted">
        <MonitorPlay size={40} className="opacity-40" />
        <p className="text-sm">{t("sillytavern:common.notRunning")}</p>
      </Card>
    );
  }

  return (
    <Card className="flex flex-col items-center justify-center gap-4 py-16">
      <MonitorPlay size={44} className="text-accent" />
      <div className="text-center">
        <p className="text-sm font-medium text-heading">
          {t("sillytavern:web.directTitle")}
        </p>
        <p className="mt-1 text-xs text-muted">
          {t("sillytavern:web.directHint")}
        </p>
        <p className="mt-2 font-mono text-xs text-muted">{status?.url}</p>
      </div>
      <a href={status?.url} target="_blank" rel="noreferrer">
        <Button variant="primary" size="md">
          <ExternalLink size={14} />
          {t("sillytavern:web.openTavern")}
        </Button>
      </a>
    </Card>
  );
}
