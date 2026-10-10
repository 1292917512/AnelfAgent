import { useRouteTab } from "@/hooks/useRouteTab";
import { lazy, Suspense } from "react";
import { PageSkeleton } from "@/components/common/AsyncState";
import { useTranslation } from "react-i18next";
import { TabBar, type TabItem } from "@/components/common/TabBar";
import { PageContainer, PageIntro } from "@/components/common/PageContainer";
import { Bot, Cpu, KeyRound, ListOrdered, Scale } from "lucide-react";
import { ConfigPanel } from "@/pages/models/ConfigPanel";

const PrioritiesPanel = lazy(() => import("@/pages/models/PrioritiesPanel").then((module) => ({ default: module.PrioritiesPanel })));
const SubAgentsPanel = lazy(() => import("@/pages/models/SubAgentsPanel").then((module) => ({ default: module.SubAgentsPanel })));
const JudgmentPanel = lazy(() => import("@/pages/models/JudgmentPanel").then((module) => ({ default: module.JudgmentPanel })));
const ProviderKeysPanel = lazy(() => import("@/components/common/ProviderKeysPanel").then((module) => ({ default: module.ProviderKeysPanel })));

type ModelTab = "config" | "priorities" | "subagents" | "judgment" | "providerKeys";

export default function Models() {
  const { t } = useTranslation(["models", "common"]);
  const [activeTab, setActiveTab] = useRouteTab<ModelTab>(["config", "priorities", "subagents", "judgment", "providerKeys"], "config");

  const tabs: TabItem<ModelTab>[] = [
    { key: "config", label: t("tabs.config"), icon: Cpu },
    { key: "priorities", label: t("tabs.priorities"), icon: ListOrdered },
    { key: "subagents", label: t("tabs.subagents"), icon: Bot },
    { key: "judgment", label: t("tabs.judgment"), icon: Scale },
    { key: "providerKeys", label: t("tabs.providerKeys"), icon: KeyRound },
  ];

  return (
    <PageContainer>
      <PageIntro />
      <TabBar tabs={tabs} activeTab={activeTab} onChange={setActiveTab} />

      <Suspense key={activeTab} fallback={<PageSkeleton />}>
        {activeTab === "config" && <ConfigPanel />}
        {activeTab === "priorities" && <PrioritiesPanel />}
        {activeTab === "subagents" && <SubAgentsPanel />}
        {activeTab === "judgment" && <JudgmentPanel />}
        {activeTab === "providerKeys" && <ProviderKeysPanel />}
      </Suspense>
    </PageContainer>
  );
}
