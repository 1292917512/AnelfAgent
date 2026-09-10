import { useState } from "react";
import { useTranslation } from "react-i18next";
import { TabBar, type TabItem } from "@/components/common/TabBar";
import { PageContainer } from "@/components/common/PageContainer";
import { cn } from "@/lib/utils";
import { Activity, HardDrive, Target, MessageSquare, StickyNote, Users, Database, Network, CalendarDays, BookOpen, Waypoints, ScrollText } from "lucide-react";
import { OverviewPanel } from "@/pages/memory/OverviewPanel";
import { STMPanel } from "@/pages/memory/STMPanel";
import { LTMPanel } from "@/pages/memory/LTMPanel";
import { GoalsPanel } from "@/pages/memory/GoalsPanel";
import { ConvPanel } from "@/pages/memory/ConvPanel";
import { EntityPanel } from "@/pages/memory/EntityPanel";
import { NotesPanel } from "@/pages/memory/NotesPanel";
import { RulesPanel } from "@/pages/memory/RulesPanel";
import { DailyNotesPanel } from "@/pages/memory/DailyNotesPanel";
import { DocsPanel } from "@/pages/memory/DocsPanel";
import { CogneePanel } from "@/pages/memory/cognee/CogneePanel";
import { GraphPanel } from "@/pages/memory/graph/GraphPanel";

type MemTab = "overview" | "stm" | "goals" | "conv" | "notes" | "daily" | "docs" | "entity" | "ltm" | "graph" | "cognee" | "rules";

/** 工作区式面板（列表 + 内容区）：md 及以上占满视口剩余高度，内部滚动 */
const FULL_HEIGHT_TABS = new Set<MemTab>(["conv", "notes", "rules"]);

export default function Memory() {
  const { t } = useTranslation("memory");
  const [tab, setTab] = useState<MemTab>("overview");

  const TAB_KEYS: TabItem<MemTab>[] = [
    { key: "overview", label: t("tabs.overview"), icon: Activity },
    { key: "stm", label: t("tabs.stm"), icon: HardDrive },
    { key: "goals", label: t("tabs.goals"), icon: Target },
    { key: "conv", label: t("tabs.conv"), icon: MessageSquare },
    { key: "notes", label: t("tabs.notes"), icon: StickyNote },
    { key: "rules", label: t("tabs.rules"), icon: ScrollText },
    { key: "daily", label: t("tabs.daily"), icon: CalendarDays },
    { key: "docs", label: t("tabs.docs"), icon: BookOpen },
    { key: "entity", label: t("tabs.entity"), icon: Users },
    { key: "ltm", label: t("tabs.ltm"), icon: Database },
    { key: "graph", label: t("tabs.graph"), icon: Waypoints },
    { key: "cognee", label: t("tabs.cognee"), icon: Network },
  ];

  return (
    <PageContainer className="min-h-full flex flex-col">
      <TabBar tabs={TAB_KEYS} activeTab={tab} onChange={setTab} />
      <div className={cn(FULL_HEIGHT_TABS.has(tab) && "md:flex-1 md:min-h-0 md:flex md:flex-col")}>
        {tab === "overview" && <OverviewPanel />}
        {tab === "stm" && <STMPanel />}
        {tab === "goals" && <GoalsPanel />}
        {tab === "conv" && <ConvPanel />}
        {tab === "notes" && <NotesPanel />}
        {tab === "rules" && <RulesPanel />}
        {tab === "daily" && <DailyNotesPanel />}
        {tab === "docs" && <DocsPanel />}
        {tab === "entity" && <EntityPanel />}
        {tab === "ltm" && <LTMPanel />}
        {tab === "graph" && <GraphPanel />}
        {tab === "cognee" && <CogneePanel />}
      </div>
    </PageContainer>
  );
}
