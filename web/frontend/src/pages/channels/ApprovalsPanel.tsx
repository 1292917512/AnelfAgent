import { useState } from "react";
import { useTranslation } from "react-i18next";
import { TabBar, type TabItem } from "@/components/common/TabBar";
import { Clock, History, Settings, BarChart3 } from "lucide-react";
import { PendingApprovalsList } from "@/pages/approvals/PendingApprovalsList";
import { ApprovalHistory } from "@/pages/approvals/ApprovalHistory";
import { PermissionRulesEditor } from "@/pages/approvals/PermissionRulesEditor";
import { ApprovalStats } from "@/pages/approvals/ApprovalStats";

type ApprovalTab = "pending" | "history" | "policies" | "stats";

export function ApprovalsPanel() {
  const { t } = useTranslation("approvals");
  const [activeTab, setActiveTab] = useState<ApprovalTab>("pending");

  const tabs: TabItem<ApprovalTab>[] = [
    { key: "pending", label: t("tabs.pending"), icon: Clock },
    { key: "history", label: t("tabs.history"), icon: History },
    { key: "policies", label: t("tabs.policies"), icon: Settings },
    { key: "stats", label: t("tabs.stats"), icon: BarChart3 },
  ];

  return (
    <div className="space-y-4">
      <TabBar tabs={tabs} activeTab={activeTab} onChange={setActiveTab} />

      {activeTab === "pending" && <PendingApprovalsList />}
      {activeTab === "history" && <ApprovalHistory />}
      {activeTab === "policies" && <PermissionRulesEditor />}
      {activeTab === "stats" && <ApprovalStats />}
    </div>
  );
}
