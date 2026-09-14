import { useState } from "react";
import { useTranslation } from "react-i18next";
import { AudioLines, FolderClock, LayoutDashboard, Settings } from "lucide-react";
import { TabBar, type TabItem } from "@/components/common/TabBar";
import { OverviewPanel } from "./panels/OverviewPanel";
import { RecordingsPanel } from "./panels/RecordingsPanel";
import { SettingsPanel } from "./panels/SettingsPanel";

type AudiosyncTab = "overview" | "recordings" | "settings";

export default function AudiosyncPanel() {
  const { t } = useTranslation("audiosync");
  const [tab, setTab] = useState<AudiosyncTab>("overview");

  const TABS: TabItem<AudiosyncTab>[] = [
    { key: "overview", label: t("tabs.overview"), icon: LayoutDashboard },
    { key: "recordings", label: t("tabs.recordings"), icon: FolderClock },
    { key: "settings", label: t("tabs.settings"), icon: Settings },
  ];

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2 text-sm text-muted">
        <AudioLines size={16} />
        <span>{t("subtitle")}</span>
      </div>
      <TabBar tabs={TABS} activeTab={tab} onChange={setTab} />
      {tab === "overview" && <OverviewPanel />}
      {tab === "recordings" && <RecordingsPanel />}
      {tab === "settings" && <SettingsPanel />}
    </div>
  );
}
