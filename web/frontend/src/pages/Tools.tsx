import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Wrench, List, FileText } from "lucide-react";
import { TabBar, type TabItem } from "@/components/common/TabBar";
import { PageContainer, PageHeader } from "@/components/common/PageContainer";
import { ToolSystemRulesPanel } from "@/pages/config/ToolSystemRulesPanel";
import { ToolsListPanel } from "@/pages/tools/ToolsListPanel";

type ToolsTab = "list" | "rules";

export default function Tools() {
  const { t } = useTranslation(["tools", "common"]);
  const [tab, setTab] = useState<ToolsTab>("list");

  const TABS: TabItem<ToolsTab>[] = [
    { key: "list", label: t("tools:tabs.list"), icon: List },
    { key: "rules", label: t("tools:tabs.rules"), icon: FileText },
  ];

  return (
    <PageContainer wide>
      <PageHeader
        icon={<Wrench size={20} className="text-accent" />}
        title={t("tools:title")}
        subtitle={t("tools:subtitle", { defaultValue: "" })}
      />
      <TabBar tabs={TABS} activeTab={tab} onChange={setTab} />
      <div className="mt-4">
        {tab === "list" && <ToolsListPanel />}
        {tab === "rules" && <ToolSystemRulesPanel />}
      </div>
    </PageContainer>
  );
}
