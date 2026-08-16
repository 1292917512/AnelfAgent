import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Bot, Puzzle, Radio, ScrollText, Settings2, Store } from "lucide-react";
import { TabBar, type TabItem } from "@/components/common/TabBar";
import { PageContainer, PageHeader } from "@/components/common/PageContainer";
import { OverviewPanel } from "@/pages/nonebot/OverviewPanel";
import { AdaptersPanel } from "@/pages/nonebot/AdaptersPanel";
import { PluginsPanel } from "@/pages/nonebot/PluginsPanel";
import { StorePanel } from "@/pages/nonebot/StorePanel";
import { EnvPanel } from "@/pages/nonebot/EnvPanel";
import { LogsPanel } from "@/pages/nonebot/LogsPanel";

type NonebotTab = "overview" | "adapters" | "plugins" | "store" | "env" | "logs";

export default function Nonebot() {
  const { t } = useTranslation("nonebot");
  const [tab, setTab] = useState<NonebotTab>("overview");

  const TABS: TabItem<NonebotTab>[] = [
    { key: "overview", label: t("tab.overview"), icon: Bot },
    { key: "adapters", label: t("tab.adapters"), icon: Radio },
    { key: "plugins", label: t("tab.plugins"), icon: Puzzle },
    { key: "store", label: t("tab.store"), icon: Store },
    { key: "env", label: t("tab.env"), icon: Settings2 },
    { key: "logs", label: t("tab.logs"), icon: ScrollText },
  ];

  return (
    <PageContainer wide>
      <PageHeader
        icon={<Bot size={20} className="text-accent" />}
        title={t("title")}
        subtitle={t("subtitle")}
      />
      <TabBar tabs={TABS} activeTab={tab} onChange={setTab} />
      <div className="mt-4">
        {tab === "overview" && <OverviewPanel />}
        {tab === "adapters" && <AdaptersPanel />}
        {tab === "plugins" && <PluginsPanel />}
        {tab === "store" && <StorePanel />}
        {tab === "env" && <EnvPanel />}
        {tab === "logs" && <LogsPanel />}
      </div>
    </PageContainer>
  );
}
