import { useState } from "react";
import { useTranslation } from "react-i18next";
import { TabBar, type TabItem } from "@/components/common/TabBar";
import { PageContainer } from "@/components/common/PageContainer";
import { Cpu, ListOrdered } from "lucide-react";
import { ConfigPanel } from "@/pages/models/ConfigPanel";
import { PrioritiesPanel } from "@/pages/models/PrioritiesPanel";

type ModelTab = "config" | "priorities";

export default function Models() {
  const { t } = useTranslation(["models", "common"]);
  const [activeTab, setActiveTab] = useState<ModelTab>("config");

  const tabs: TabItem<ModelTab>[] = [
    { key: "config", label: t("tabs.config"), icon: Cpu },
    { key: "priorities", label: t("tabs.priorities"), icon: ListOrdered },
  ];

  return (
    <PageContainer wide>
      <TabBar tabs={tabs} activeTab={activeTab} onChange={setActiveTab} />

      {activeTab === "config" ? <ConfigPanel /> : <PrioritiesPanel />}
    </PageContainer>
  );
}
