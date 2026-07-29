import { useState } from "react";
import { useTranslation } from "react-i18next";
import { TabBar } from "@/components/common/TabBar";
import { Activity, History, Database } from "lucide-react";
import { MonitorTab } from "./context/MonitorTab";
import { HistoryTab } from "./context/HistoryTab";
import { ProvidersTab } from "./context/ProvidersTab";

type ContextTab = "monitor" | "history" | "providers";

// ======================================================================
// 实时监控 Tab
// ======================================================================

interface ContextProps {
  hideHeader?: boolean;
}

export default function Context({ hideHeader = false }: ContextProps) {
  const { t } = useTranslation("context");
  const [tab, setTab] = useState<ContextTab>("monitor");

  const tabs = [
    { key: "monitor" as ContextTab, label: t("tabs.monitor"), icon: Activity },
    { key: "history" as ContextTab, label: t("tabs.history"), icon: History },
    { key: "providers" as ContextTab, label: t("tabs.providers"), icon: Database },
  ];

  return (
    <div className="h-full flex flex-col">
      {!hideHeader && (
        <>
          <div className="px-4 md:px-6 py-4 border-b border-border">
            <h1 className="text-lg font-semibold text-heading">{t("title")}</h1>
            <p className="text-xs text-muted mt-0.5">{t("subtitle")}</p>
          </div>
          <div className="px-4 md:px-6 border-b border-border">
            <TabBar tabs={tabs} activeTab={tab} onChange={setTab} />
          </div>
        </>
      )}
      {hideHeader && (
        <div className="px-4 md:px-6 border-b border-border">
          <TabBar tabs={tabs} activeTab={tab} onChange={setTab} />
        </div>
      )}
      <div className="flex-1 overflow-y-auto p-4 md:p-6">
        {tab === "monitor" && <MonitorTab />}
        {tab === "history" && <HistoryTab />}
        {tab === "providers" && <ProvidersTab />}
      </div>
    </div>
  );
}
