import { useState } from "react";
import { useTranslation } from "react-i18next";
import { MousePointerClick } from "lucide-react";
import { PageContainer, PageHeader } from "@/components/common/PageContainer";
import { TabBar, type TabItem } from "@/components/common/TabBar";
import { OperationActionsPanel } from "@/pages/operation/OperationActionsPanel";
import { McpLinkagePanel } from "@/pages/operation/McpLinkagePanel";

type OperationTab = "actions" | "mcp";

export default function Operation() {
  const { t } = useTranslation("operation");
  const [tab, setTab] = useState<OperationTab>("actions");
  const TABS: TabItem[] = [
    { key: "actions", label: t("tabs.actions") },
    { key: "mcp", label: t("tabs.mcp") },
  ];

  return (
    <PageContainer>
      <PageHeader
        icon={<MousePointerClick size={20} className="text-accent" />}
        title={t("title")}
        subtitle={t("subtitle")}
      />
      <TabBar tabs={TABS} activeTab={tab} onChange={(k) => setTab(k as OperationTab)} />
      {tab === "actions" && <OperationActionsPanel />}
      {tab === "mcp" && <McpLinkagePanel />}
    </PageContainer>
  );
}
