/** 智能家居控制台：设备、平台连接与 AI 感知设置。 */
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { AlertCircle, ArrowUpRight, Home, Layers3, PlugZap, RefreshCw, Sparkles } from "lucide-react";
import { TabBar, type TabItem } from "@/components/common/TabBar";
import { Button, LoadingBlock } from "@/components/ui";
import { DevicesPanel } from "./panels/DevicesPanel";
import { DomainsPanel } from "./panels/DomainsPanel";
import { ProvidersPanel } from "./panels/ProvidersPanel";
import { useSmartHomeData } from "./panels/useSmartHomeData";

type HomeTab = "devices" | "providers" | "domains";

export default function SmartHomePanel() {
  const { t } = useTranslation("smart_home");
  const data = useSmartHomeData();
  const [tab, setTab] = useState<HomeTab>("devices");
  if (data.isError) return (
    <div className="rounded-2xl border border-border bg-card p-10 text-center">
      <AlertCircle className="mx-auto mb-4 h-8 w-8 text-warn" />
      <p className="mb-4 text-sm text-muted">{t("loadFailed")}</p>
      <Button onClick={data.refreshAll}>{t("retry")}</Button>
    </div>
  );
  if (data.isLoading || !data.status) return <LoadingBlock />;
  const devices = data.devices.filter((device) => data.domainByHa.has(device.domain));
  const presentDomains = data.domains.flatMap((domain) => {
    const matching = devices.filter((device) => domain.ha_domains.includes(device.domain));
    return matching.length ? [{ ...domain, device_count: matching.length, available_count: matching.filter((device) => device.available).length }] : [];
  });
  const activeProviders = data.status.providers.filter((provider) => provider.configured || provider.connected || provider.device_count > 0);
  const online = devices.filter((device) => device.available).length;
  const rooms = new Set(devices.map((device) => device.area).filter(Boolean)).size;
  const active = devices.filter((device) => device.available && ["on", "playing", "cool", "heat", "open"].includes(device.state)).length;
  const tabs: TabItem<HomeTab>[] = [
    { key: "devices", label: t("tabs.devices"), icon: Home },
    { key: "providers", label: t("tabs.providers"), icon: PlugZap },
    { key: "domains", label: t("tabs.domains"), icon: Sparkles },
  ];
  return (
    <div className="space-y-6 pb-4">
      <header className="overflow-hidden rounded-2xl border border-border bg-card">
        <div className="flex flex-wrap items-start justify-between gap-4 p-5 sm:p-6">
          <div className="flex items-center gap-3.5">
            <div className="grid h-12 w-12 shrink-0 place-items-center rounded-2xl bg-accent-subtle text-accent"><Home size={23} strokeWidth={1.6} /></div>
            <div><h2 className="text-xl font-semibold tracking-tight text-heading">{t("home.title")}</h2><p className="mt-1 text-xs leading-relaxed text-muted">{t("home.subtitle")}</p></div>
          </div>
          <Button size="sm" variant="ghost" onClick={data.refreshAll} loading={data.refreshing}><RefreshCw size={14} />{t("home.refresh")}</Button>
        </div>
        {devices.length > 0 && <div className="grid grid-cols-3 divide-x divide-border border-y border-border bg-elevated/40">
          {[[t("home.available"), String(online), "/ " + devices.length], [t("home.rooms"), String(rooms), ""], [t("home.active"), String(active), ""]].map(([label, value, suffix]) => (
            <div key={label} className="px-4 py-4 sm:px-6"><p className="text-xs text-muted">{label}</p><p className="mt-1.5 text-2xl font-semibold tabular-nums tracking-tight text-heading">{value}<span className="ml-1.5 text-sm font-normal text-muted">{suffix}</span></p></div>
          ))}
        </div>}
        {activeProviders.length > 0 && <div className="flex flex-wrap items-center justify-between gap-3 px-5 py-3">
          <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
            {activeProviders.map((provider) => (
              <button key={provider.provider} onClick={() => setTab("providers")} className="flex items-center gap-2 text-xs text-muted transition-colors hover:text-heading">
                <span className={"h-1.5 w-1.5 rounded-full " + (provider.connected ? "bg-ok" : provider.configured ? "bg-warn" : "bg-border-strong")} />
                {provider.provider_name}<ArrowUpRight size={12} />
              </button>
            ))}
          </div>
          <span className={"flex items-center gap-1.5 text-[11px] " + (data.streamConnected ? "text-muted" : "text-warn")}><Layers3 size={12} />{t(data.streamConnected ? "home.live" : "home.reconnecting")}</span>
        </div>}
      </header>
      {data.hasRefreshError && <div role="status" className="rounded-xl border border-warn/30 bg-warn-subtle px-4 py-3 text-xs text-warn">{t("home.refreshFailed")}</div>}
      <TabBar tabs={tabs} activeTab={tab} onChange={setTab} />
      {tab === "devices" && <DevicesPanel devices={devices} domains={presentDomains} domainByHa={data.domainByHa} connected={data.status.connected} pendingIds={data.pendingIds} feedback={data.feedback} onControl={data.control} onConnect={() => setTab("providers")} />}
      {tab === "providers" && <ProvidersPanel providers={activeProviders} availableProviders={data.status.providers.filter((provider) => !activeProviders.includes(provider))} onChanged={data.refreshAll} />}
      {tab === "domains" && <DomainsPanel domains={presentDomains} preview={data.preview} onRefreshPreview={data.refreshPreview} onSaveConfig={data.saveConfig} />}
    </div>
  );
}
