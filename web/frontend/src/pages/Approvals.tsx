import { useRouteTab } from "@/hooks/useRouteTab";
import { useTranslation } from "react-i18next";
import { Shield, History, Settings } from "lucide-react";
import { TabBar, type TabItem } from "@/components/common/TabBar";
import { PageContainer, PageHeader } from "@/components/common/PageContainer";
import { ApprovalHistory } from "@/pages/approvals/ApprovalHistory";
import { PermissionRulesEditor } from "@/pages/approvals/PermissionRulesEditor";
import { ApprovalStatsStrip } from "@/pages/approvals/ApprovalStatsStrip";

type ApprovalTab = "rules" | "history";

export default function Approvals() {
  const { t } = useTranslation("approvals");
  const [activeTab, setActiveTab] = useRouteTab<ApprovalTab>(["rules", "history"], "rules");
  const tabs: TabItem<ApprovalTab>[] = [
    { key: "rules", label: t("tabs.rules"), icon: Settings },
    { key: "history", label: t("tabs.history"), icon: History },
  ];
  return (
    <PageContainer>
      <PageHeader icon={<Shield size={20} className="text-accent" />} title={t("pageTitle")} subtitle={t("pageSubtitle")} />
      <ApprovalStatsStrip />
      <TabBar tabs={tabs} activeTab={activeTab} onChange={setActiveTab} />
      {activeTab === "history" ? <ApprovalHistory /> : <PermissionRulesEditor />}
    </PageContainer>
  );
}
