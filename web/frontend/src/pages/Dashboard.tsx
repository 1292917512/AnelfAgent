import { useSearchParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { TabBar, type TabItem } from "@/components/common/TabBar";
import { PageContainer } from "@/components/common/PageContainer";
import { Activity, ScrollText } from "lucide-react";
import { OverviewPanel } from "@/pages/dashboard/OverviewPanel";
import { LogsPanel } from "@/pages/dashboard/LogsPanel";

type DashTab = "overview" | "logs";

const VALID_TABS: DashTab[] = ["overview", "logs"];

export default function Dashboard() {
  const { t } = useTranslation(["dashboard", "common", "status"]);
  const [searchParams, setSearchParams] = useSearchParams();
  const tabParam = searchParams.get("tab") as DashTab | null;
  const tab: DashTab = tabParam && VALID_TABS.includes(tabParam) ? tabParam : "overview";

  const changeTab = (next: DashTab) => {
    setSearchParams(next === "overview" ? {} : { tab: next }, { replace: true });
  };

  const TAB_KEYS: TabItem<DashTab>[] = [
    { key: "overview", label: t("tabs.overview"), icon: Activity },
    { key: "logs", label: t("tabs.logs"), icon: ScrollText },
  ];

  return (
    <PageContainer wide>
      <TabBar tabs={TAB_KEYS} activeTab={tab} onChange={changeTab} />
      {tab === "overview" && <OverviewPanel />}
      {tab === "logs" && <LogsPanel />}
    </PageContainer>
  );
}
