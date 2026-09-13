import { useState } from "react";
import { useTranslation } from "react-i18next";
import { TabBar, type TabItem } from "@/components/common/TabBar";
import { Activity, Link2, Plus, ScrollText } from "lucide-react";
import { OverviewPanel } from "./panels/OverviewPanel";
import { LinksPanel } from "./panels/LinksPanel";
import { CreateDialog } from "./panels/CreateDialog";
import { LogsPanel } from "./panels/LogsPanel";

type ShareTab = "overview" | "links" | "create" | "logs";

export default function SharePanel() {
  const { t } = useTranslation("share");
  const [tab, setTab] = useState<ShareTab>("overview");

  const TABS: TabItem<ShareTab>[] = [
    { key: "overview", label: t("tabs.overview"), icon: Activity },
    { key: "links", label: t("tabs.links"), icon: Link2 },
    { key: "create", label: t("tabs.create"), icon: Plus },
    { key: "logs", label: t("tabs.logs"), icon: ScrollText },
  ];

  return (
    <div className="space-y-4">
      <TabBar tabs={TABS} activeTab={tab} onChange={setTab} />
      {tab === "overview" && <OverviewPanel />}
      {tab === "links" && <LinksPanel />}
      {tab === "create" && <CreateDialog />}
      {tab === "logs" && <LogsPanel />}
    </div>
  );
}
