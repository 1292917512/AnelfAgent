import { registerPluginI18n } from "@/lib/plugin-i18n";
import zh from "./voiceprint/locales/zh.json";
import en from "./voiceprint/locales/en.json";

registerPluginI18n("voiceprint", { zh, en });

import { useState } from "react";
import { useTranslation } from "react-i18next";
import {
  AudioLines, Clock3, FolderClock, Inbox, LayoutDashboard, Search, Settings, Users,
} from "lucide-react";
import { TabBar, type TabItem } from "@/components/common/TabBar";
import { OverviewPanel } from "./voiceprint/OverviewPanel";
import { SpeakersPanel } from "./voiceprint/SpeakersPanel";
import { RecordingsPanel } from "./voiceprint/RecordingsPanel";
import { TimelinePanel } from "./voiceprint/TimelinePanel";
import { TranscriptsPanel } from "./voiceprint/TranscriptsPanel";
import { IngestPanel } from "./voiceprint/IngestPanel";
import { SettingsPanel } from "./voiceprint/SettingsPanel";

type VoiceprintTab =
  "overview" | "speakers" | "recordings" | "timeline" | "transcripts" | "ingest" | "settings";

export default function VoiceprintPanel() {
  const { t } = useTranslation("voiceprint");
  const [tab, setTab] = useState<VoiceprintTab>("overview");

  const TABS: TabItem<VoiceprintTab>[] = [
    { key: "overview", label: t("tabs.overview"), icon: LayoutDashboard },
    { key: "speakers", label: t("tabs.speakers"), icon: Users },
    { key: "recordings", label: t("tabs.recordings"), icon: FolderClock },
    { key: "timeline", label: t("tabs.timeline"), icon: Clock3 },
    { key: "transcripts", label: t("tabs.transcripts"), icon: Search },
    { key: "ingest", label: t("tabs.ingest"), icon: Inbox },
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
      {tab === "speakers" && <SpeakersPanel />}
      {tab === "recordings" && <RecordingsPanel />}
      {tab === "timeline" && <TimelinePanel />}
      {tab === "transcripts" && <TranscriptsPanel />}
      {tab === "ingest" && <IngestPanel />}
      {tab === "settings" && <SettingsPanel />}
    </div>
  );
}
