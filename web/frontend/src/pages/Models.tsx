import { useState } from "react";
import { useTranslation } from "react-i18next";
import { TabBar, type TabItem } from "@/components/common/TabBar";
import { PageContainer } from "@/components/common/PageContainer";
import { Bot, Cpu, KeyRound, ListOrdered } from "lucide-react";
import { ConfigPanel } from "@/pages/models/ConfigPanel";
import { PrioritiesPanel } from "@/pages/models/PrioritiesPanel";
import { SubAgentsPanel } from "@/pages/models/SubAgentsPanel";
import { ProviderKeysPanel } from "@/components/common/ProviderKeysPanel";

type ModelTab = "config" | "priorities" | "subagents" | "providerKeys";

export default function Models() {
  const { t } = useTranslation(["models", "common"]);
  const [activeTab, setActiveTab] = useState<ModelTab>("config");

  const tabs: TabItem<ModelTab>[] = [
    { key: "config", label: t("tabs.config"), icon: Cpu },
    { key: "priorities", label: t("tabs.priorities"), icon: ListOrdered },
    { key: "subagents", label: t("tabs.subagents"), icon: Bot },
    { key: "providerKeys", label: t("tabs.providerKeys"), icon: KeyRound },
  ];

  return (
    <PageContainer>
      <TabBar tabs={tabs} activeTab={activeTab} onChange={setActiveTab} />

      {activeTab === "config" && <ConfigPanel />}
      {activeTab === "priorities" && <PrioritiesPanel />}
      {activeTab === "subagents" && <SubAgentsPanel />}
      {activeTab === "providerKeys" && <ProviderKeysPanel />}
    </PageContainer>
  );
}
