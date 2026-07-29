import { useState } from "react";
import { useTranslation } from "react-i18next";
import { TabBar, type TabItem } from "@/components/common/TabBar";
import { PageContainer } from "@/components/common/PageContainer";
import { Activity, Link2, Plus, Settings2, ScrollText } from "lucide-react";
import { OverviewPanel } from "@/pages/share/OverviewPanel";
import { LinksPanel } from "@/pages/share/LinksPanel";
import { CreateDialog } from "@/pages/share/CreateDialog";
import { ConfigPanel } from "@/pages/share/ConfigPanel";
import { LogsPanel } from "@/pages/share/LogsPanel";

type ShareTab = "overview" | "links" | "create" | "logs" | "config";

export default function Share() {
  const { t } = useTranslation("share");
  const [tab, setTab] = useState<ShareTab>("overview");

  const TABS: TabItem<ShareTab>[] = [
    { key: "overview", label: t("tabs.overview"), icon: Activity },
    { key: "links", label: t("tabs.links"), icon: Link2 },
    { key: "create", label: t("tabs.create"), icon: Plus },
    { key: "logs", label: t("tabs.logs"), icon: ScrollText },
    { key: "config", label: t("tabs.config"), icon: Settings2 },
  ];

  return (
    <PageContainer wide>
      <TabBar tabs={TABS} activeTab={tab} onChange={setTab} />
      {tab === "overview" && <OverviewPanel />}
      {tab === "links" && <LinksPanel />}
      {tab === "create" && <CreateDialog />}
      {tab === "logs" && <LogsPanel />}
      {tab === "config" && <ConfigPanel />}
    </PageContainer>
  );
}
