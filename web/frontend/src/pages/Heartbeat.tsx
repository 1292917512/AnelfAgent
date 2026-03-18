import { useState } from "react";
import { useTranslation } from "react-i18next";
import { TabBar, type TabItem } from "@/components/common/TabBar";
import { Activity, Settings2 } from "lucide-react";
import { StatusPanel } from "@/pages/heartbeat/StatusPanel";
import { ConfigPanel } from "@/pages/heartbeat/ConfigPanel";

type HeartbeatTab = "status" | "config";

export default function Heartbeat() {
  const { t } = useTranslation("heartbeat");
  const [tab, setTab] = useState<HeartbeatTab>("status");

  const TABS: TabItem<HeartbeatTab>[] = [
    { key: "status", label: t("tabs.status"), icon: Activity },
    { key: "config", label: t("tabs.config"), icon: Settings2 },
  ];

  return (
    <div className="space-y-6 max-w-6xl">
      <TabBar tabs={TABS} activeTab={tab} onChange={setTab} />
      {tab === "status" && <StatusPanel />}
      {tab === "config" && <ConfigPanel />}
    </div>
  );
}
