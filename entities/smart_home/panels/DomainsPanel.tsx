/** AI 感知设置与实际注入预览，共用统一配置接口。 */
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useQuery } from "@tanstack/react-query";
import { Sparkles } from "lucide-react";
import { Switch } from "@/components/ui";
import { toast } from "@/stores/toast-store";
import { ProviderConfigForm } from "./ProviderConfigForm";
import { DomainCard } from "./DomainCard";
import { PreviewBlock } from "./PreviewBlock";
import { smartHomeApi } from "./api";
import type { DeviceDomainInfo, PreviewResponse } from "./types";

export function DomainsPanel({ domains, preview, onRefreshPreview, onSaveConfig }: {
  domains: DeviceDomainInfo[]; preview?: PreviewResponse; onRefreshPreview: () => void;
  onSaveConfig: (key: string, value: unknown) => Promise<void>;
}) {
  const { t } = useTranslation("smart_home");
  const [saving, setSaving] = useState(false);
  const config = useQuery({ queryKey: ["smart-home-entity-config"], queryFn: () => smartHomeApi.getConfig().then((r) => r.data) });
  const enabled = config.data?.items.find((item) => item.key === "smart_home_context_inject")?.value === true;
  const toggle = async (value: boolean) => {
    setSaving(true);
    try { await onSaveConfig("smart_home_context_inject", value); }
    catch { toast.error(t("config.saveFailed")); }
    finally { setSaving(false); }
  };
  return <div className="space-y-5">
    <div className="flex items-center justify-between gap-4 rounded-2xl border border-border bg-card p-5">
      <div className="flex items-center gap-3"><Sparkles className="h-5 w-5 shrink-0 text-accent" /><div><h3 className="text-sm font-semibold text-heading">{t("domains.awareness")}</h3><p className="mt-1.5 text-xs leading-relaxed text-muted">{t("domains.awarenessHint")}</p></div></div>
      <Switch checked={enabled} label={t("domains.awareness")} disabled={saving || !config.data} onChange={(value) => void toggle(value)} />
    </div>
    {config.data && <div className="rounded-2xl border border-border bg-card p-5"><ProviderConfigForm items={config.data.items.filter((item) => item.key.startsWith("smart_home_context_") && item.key !== "smart_home_context_inject")} onSaved={() => { void config.refetch(); onRefreshPreview(); }} /></div>}
    {config.isError && <p role="alert" className="text-xs text-danger">{t("config.loadFailed")}</p>}
    <div className="grid items-start gap-5 xl:grid-cols-2">
      <div className="space-y-3">
        <div className="pb-1"><h3 className="text-sm font-semibold text-heading">{t("domains.title", { count: domains.length })}</h3><p className="mt-1 text-xs leading-relaxed text-muted">{t("domains.hint")}</p></div>
        {domains.length ? domains.map((domain) => <DomainCard key={domain.key} domain={domain} onSaveConfig={onSaveConfig} />)
          : <p className="rounded-xl border border-dashed border-border p-5 text-xs leading-relaxed text-muted">{t("domains.empty")}</p>}
      </div>
      <div className="xl:sticky xl:top-4"><PreviewBlock preview={preview} onRefresh={onRefreshPreview} /></div>
    </div>
  </div>;
}
