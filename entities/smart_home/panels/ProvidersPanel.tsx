/** 连接页签 — 各外部接入平台一张组件卡（状态/详情/连接配置/操作）。 */
import { useTranslation } from "react-i18next";
import { useQuery } from "@tanstack/react-query";
import { Server } from "lucide-react";
import { Card } from "@/components/common/Card";
import { smartHomeApi } from "./api";
import { ProviderCard } from "./ProviderCard";
import type { StatusResponse } from "./types";

interface ProvidersPanelProps {
  status: StatusResponse;
  onChanged: () => void;
}

export function ProvidersPanel({ status, onChanged }: ProvidersPanelProps) {
  const { t } = useTranslation("smart_home");
  const configQuery = useQuery({
    queryKey: ["smart-home-entity-config"],
    queryFn: () => smartHomeApi.getConfig().then((r) => r.data),
  });
  const itemsFor = (providerKey: string) =>
    (configQuery.data?.items ?? []).filter((item) =>
      item.key.startsWith(`smart_home_${providerKey}_`),
    );

  return (
    <Card
      title={t("providers.title", { count: status.providers.length })}
      subtitle={t("providers.hint", { count: status.providers.length })}
      actions={
        <span className="flex items-center gap-1.5 text-[11px] text-muted">
          <Server className="h-3.5 w-3.5" />
          {t("providers.summary", {
            connected: status.providers.filter((p) => p.connected).length,
            total: status.providers.length,
          })}
        </span>
      }
    >
      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
        {status.providers.map((provider) => (
          <ProviderCard
            key={provider.provider}
            provider={provider}
            configItems={itemsFor(provider.provider)}
            onChanged={onChanged}
          />
        ))}
      </div>
    </Card>
  );
}
