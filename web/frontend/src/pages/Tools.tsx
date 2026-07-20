import { useState } from "react";
import { useTranslation } from "react-i18next";
import { List, FileText } from "lucide-react";
import { TabBar, type TabItem } from "@/components/common/TabBar";
import { PageContainer } from "@/components/common/PageContainer";
import { ToolSystemRulesPanel } from "@/pages/config/ToolSystemRulesPanel";
import { ToolsListPanel } from "./tools/ToolsListPanel";

type ToolsTab = "list" | "rules";

export default function Tools() {
  const { t } = useTranslation("tools");
  const [activeTab, setActiveTab] = useState<ToolsTab>("list");

  const TABS: TabItem<ToolsTab>[] = [
    { key: "list", label: t("tabs.list"), icon: List },
    { key: "rules", label: t("tabs.rules"), icon: FileText },
  ];

  return (
    <PageContainer wide>
      <TabBar tabs={TABS} activeTab={activeTab} onChange={setActiveTab} />
      {activeTab === "list" && <ToolsListPanel />}
      {activeTab === "rules" && <ToolSystemRulesPanel />}
    </PageContainer>
  );
}
