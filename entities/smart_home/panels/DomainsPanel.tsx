/** 组件与注入页签 — 设备域组件管理（启停/配置）+ AI 注入预览。 */
import { useTranslation } from "react-i18next";
import { Boxes } from "lucide-react";
import { Card } from "@/components/common/Card";
import { DomainCard } from "./DomainCard";
import { PreviewBlock } from "./PreviewBlock";
import type { DeviceDomainInfo, PreviewResponse } from "./types";

interface DomainsPanelProps {
  domains: DeviceDomainInfo[];
  preview?: PreviewResponse;
  onRefreshPreview: () => void;
  onSaveConfig: (key: string, value: unknown) => Promise<void>;
}

export function DomainsPanel({
  domains,
  preview,
  onRefreshPreview,
  onSaveConfig,
}: DomainsPanelProps) {
  const { t } = useTranslation("smart_home");
  return (
    <div className="space-y-4">
      <Card
        title={t("domains.title", { count: domains.length })}
        subtitle={t("domains.hint", { count: domains.length })}
        actions={<Boxes className="h-4 w-4 text-muted" />}
      >
        <div className="grid gap-3 xl:grid-cols-2">
          {domains.map((domain) => (
            <DomainCard key={domain.key} domain={domain} onSaveConfig={onSaveConfig} />
          ))}
        </div>
        <p className="mt-3 text-[10px] text-muted">{t("domains.footerHint")}</p>
      </Card>
      <PreviewBlock preview={preview} onRefresh={onRefreshPreview} />
    </div>
  );
}
