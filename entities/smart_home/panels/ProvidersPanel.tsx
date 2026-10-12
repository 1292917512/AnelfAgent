/** 展示已接入的平台；未接入平台仅在主动展开添加入口后显示。 */
import { useTranslation } from "react-i18next";
import { useQuery } from "@tanstack/react-query";
import { Button, LoadingBlock } from "@/components/ui";
import { smartHomeApi } from "./api";
import { ProviderCard } from "./ProviderCard";
import type { ProviderStatus } from "./types";

export function ProvidersPanel({ providers, availableProviders, onChanged }: {
  providers: ProviderStatus[]; availableProviders: ProviderStatus[]; onChanged: () => void;
}) {
  const { t } = useTranslation("smart_home");
  const configQuery = useQuery({ queryKey: ["smart-home-entity-config"], queryFn: () => smartHomeApi.getConfig().then((r) => r.data) });
  const cards = (items: ProviderStatus[]) => <div className="grid items-start gap-4 lg:grid-cols-2 2xl:grid-cols-3">
    {items.map((provider) => <ProviderCard key={provider.provider} provider={provider} configItems={(configQuery.data?.items ?? []).filter((item) => item.key.startsWith("smart_home_" + provider.provider + "_"))} onChanged={onChanged} />)}
  </div>;
  return <div className="space-y-5">
    <div><h3 className="text-sm font-semibold text-heading">{t("providers.title")}</h3><p className="mt-1.5 text-xs leading-relaxed text-muted">{t("providers.hint")}</p></div>
    {configQuery.isPending ? <LoadingBlock /> : configQuery.isError ? <div role="alert" className="flex items-center justify-between gap-3 rounded-xl bg-danger-subtle p-4 text-xs text-danger">{t("config.loadFailed")}<Button size="sm" onClick={() => void configQuery.refetch()}>{t("retry")}</Button></div> : <>
      {providers.length ? cards(providers) : <p className="rounded-xl border border-dashed border-border p-5 text-sm text-muted">{t("providers.empty")}</p>}
      {availableProviders.length > 0 && <details className="rounded-2xl border border-border bg-card p-5">
        <summary className="cursor-pointer text-sm font-medium text-heading">{t("providers.add")}</summary>
        <p className="mb-4 mt-3 text-xs leading-relaxed text-muted">{t("providers.addHint")}</p>
        {cards(availableProviders)}
      </details>}
    </>}
  </div>;
}
