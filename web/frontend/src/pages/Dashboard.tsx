import { useRouteTab } from "@/hooks/useRouteTab";
import { useTranslation } from "react-i18next";
import { TabBar, type TabItem } from "@/components/common/TabBar";
import { PageContainer, PageIntro } from "@/components/common/PageContainer";
import { Activity, ScrollText } from "lucide-react";
import { OverviewPanel } from "@/pages/dashboard/OverviewPanel";
import { LogsPanel } from "@/pages/dashboard/LogsPanel";
import { ContributionSlot } from "@/components/extensions/ContributionSlot";
import { SectionBoundary } from "@/components/common/SectionBoundary";

type DashTab = "overview" | "logs";

const VALID_TABS: DashTab[] = ["overview", "logs"];

export default function Dashboard() {
  const { t } = useTranslation(["dashboard", "common", "status"]);
  const [tab, changeTab] = useRouteTab(VALID_TABS, "overview");

  const TAB_KEYS: TabItem<DashTab>[] = [
    { key: "overview", label: t("tabs.overview"), icon: Activity },
    { key: "logs", label: t("tabs.logs"), icon: ScrollText },
  ];

  return (
    <PageContainer>
      <PageIntro />
      <TabBar tabs={TAB_KEYS} activeTab={tab} onChange={changeTab} />
      {tab === "overview" && <>
        <ContributionSlot slot="dashboard.cards" />
        <SectionBoundary><OverviewPanel /></SectionBoundary>
      </>}
      {tab === "logs" && <LogsPanel />}
    </PageContainer>
  );
}
