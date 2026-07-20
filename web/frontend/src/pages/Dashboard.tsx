import { useState } from "react";
import { useTranslation } from "react-i18next";
import { TabBar, type TabItem } from "@/components/common/TabBar";
import { PageContainer } from "@/components/common/PageContainer";
import { Activity, BarChart3, Zap, ScrollText } from "lucide-react";
import { OverviewPanel } from "@/pages/dashboard/OverviewPanel";
import { ToolsInsightPanel } from "@/pages/dashboard/ToolsInsightPanel";
import { EventsPanel } from "@/pages/dashboard/EventsPanel";
import { LogsPanel } from "@/pages/dashboard/LogsPanel";

type DashTab = "overview" | "tools" | "events" | "logs";

export default function Dashboard() {
  const { t } = useTranslation(["dashboard", "common", "status"]);
  const [tab, setTab] = useState<DashTab>("overview");

  const TAB_KEYS: TabItem<DashTab>[] = [
    { key: "overview", label: t("tabs.overview"), icon: Activity },
    { key: "tools", label: t("tabs.tools"), icon: BarChart3 },
    { key: "events", label: t("tabs.events"), icon: Zap },
    { key: "logs", label: t("tabs.logs"), icon: ScrollText },
  ];

  return (
    <PageContainer wide>
      <TabBar tabs={TAB_KEYS} activeTab={tab} onChange={setTab} />
      {tab === "overview" && <OverviewPanel />}
      {tab === "tools" && <ToolsInsightPanel />}
      {tab === "events" && <EventsPanel />}
      {tab === "logs" && <LogsPanel />}
    </PageContainer>
  );
}
