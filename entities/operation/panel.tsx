import { useState } from "react";
import { useTranslation } from "react-i18next";
import { TabBar, type TabItem } from "@/components/common/TabBar";
import { McpLinkagePanel } from "./panels/McpLinkagePanel";
import { OperationActionsPanel } from "./panels/OperationActionsPanel";

type OperationTab = "actions" | "mcp";

export default function OperationPanel() {
  const { t } = useTranslation("operation");
  const [tab, setTab] = useState<OperationTab>("actions");
  const TABS: TabItem[] = [
    { key: "actions", label: t("tabs.actions") },
    { key: "mcp", label: t("tabs.mcp") },
  ];

  return (
    <div className="space-y-4">
      <TabBar tabs={TABS} activeTab={tab} onChange={(k) => setTab(k as OperationTab)} />
      {tab === "actions" && <OperationActionsPanel />}
      {tab === "mcp" && <McpLinkagePanel />}
    </div>
  );
}
