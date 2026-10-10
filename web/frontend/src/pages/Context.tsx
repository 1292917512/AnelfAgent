import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { TabBar } from "@/components/common/TabBar";
import { Activity, History, Database, ArrowUpRight } from "lucide-react";
import { useRouteTab } from "@/hooks/useRouteTab";
import { MonitorTab } from "./context/MonitorTab";
import { HistoryTab } from "./context/HistoryTab";
import { ProvidersPanel } from "@/components/context/ProvidersPanel";

const CONTEXT_TABS = ["monitor", "history", "providers"] as const;
type ContextTab = typeof CONTEXT_TABS[number];

export default function Context() {
  const { t } = useTranslation("context");
  const [tab, setTab] = useRouteTab(CONTEXT_TABS, "monitor");

  const tabs = [
    { key: "monitor", label: t("tabs.monitor"), icon: Activity },
    { key: "history", label: t("tabs.history"), icon: History },
    { key: "providers", label: t("tabs.providers"), icon: Database },
  ] satisfies { key: ContextTab; label: string; icon: typeof Activity }[];

  return (
    <div className="h-full min-h-0 flex flex-col">
      <div className="flex items-start justify-between gap-4 px-4 md:px-6 py-4 border-b border-border bg-card">
        <div>
          <h1 className="text-lg font-semibold text-heading">{t("title")}</h1>
          <p className="text-xs leading-relaxed text-muted mt-1">{t("subtitle")}</p>
        </div>
        <Link to="/thinking" className="inline-flex shrink-0 items-center gap-1 py-2 text-xs text-accent">
          {t("thinking:title")}<ArrowUpRight size={14} />
        </Link>
      </div>
      <div className="px-4 md:px-6 border-b border-border">
        <TabBar tabs={tabs} activeTab={tab} onChange={setTab} />
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto p-4 md:p-6">
        <div className="mx-auto max-w-6xl">
          {tab === "monitor" && <MonitorTab />}
          {tab === "history" && <HistoryTab />}
          {tab === "providers" && <ProvidersPanel />}
        </div>
      </div>
    </div>
  );
}
