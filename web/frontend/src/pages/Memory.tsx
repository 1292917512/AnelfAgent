import { useState } from "react";
import { useTranslation } from "react-i18next";
import { TabBar, type TabItem } from "@/components/common/TabBar";
import { Activity, HardDrive, Target, MessageSquare, StickyNote, Users, Database, Settings2, Network } from "lucide-react";
import { OverviewPanel } from "@/pages/memory/OverviewPanel";
import { STMPanel } from "@/pages/memory/STMPanel";
import { LTMPanel } from "@/pages/memory/LTMPanel";
import { GoalsPanel } from "@/pages/memory/GoalsPanel";
import { ConvPanel } from "@/pages/memory/ConvPanel";
import { EntityPanel } from "@/pages/memory/EntityPanel";
import { NotesPanel } from "@/pages/memory/NotesPanel";
import { ConfigPanel } from "@/pages/memory/ConfigPanel";
import { CogneePanel } from "@/pages/memory/cognee/CogneePanel";

type MemTab = "overview" | "stm" | "goals" | "conv" | "notes" | "entity" | "ltm" | "cognee" | "config";

export default function Memory() {
  const { t } = useTranslation("memory");
  const [tab, setTab] = useState<MemTab>("overview");

  const TAB_KEYS: TabItem<MemTab>[] = [
    { key: "overview", label: t("tabs.overview"), icon: Activity },
    { key: "stm", label: t("tabs.stm"), icon: HardDrive },
    { key: "goals", label: t("tabs.goals"), icon: Target },
    { key: "conv", label: t("tabs.conv"), icon: MessageSquare },
    { key: "notes", label: t("tabs.notes"), icon: StickyNote },
    { key: "entity", label: t("tabs.entity"), icon: Users },
    { key: "ltm", label: t("tabs.ltm"), icon: Database },
    { key: "cognee", label: t("tabs.cognee"), icon: Network },
    { key: "config", label: t("tabs.config"), icon: Settings2 },
  ];

  return (
    <div className="space-y-6 max-w-6xl">
      <TabBar tabs={TAB_KEYS} activeTab={tab} onChange={setTab} />
      {tab === "overview" && <OverviewPanel />}
      {tab === "stm" && <STMPanel />}
      {tab === "goals" && <GoalsPanel />}
      {tab === "conv" && <ConvPanel />}
      {tab === "notes" && <NotesPanel />}
      {tab === "entity" && <EntityPanel />}
      {tab === "ltm" && <LTMPanel />}
      {tab === "cognee" && <CogneePanel />}
      {tab === "config" && <ConfigPanel />}
    </div>
  );
}
