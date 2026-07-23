import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useQuery } from "@tanstack/react-query";
import { Shield, Clock, History, Settings } from "lucide-react";
import { approvalsApi } from "@/lib/api";
import { TabBar, type TabItem } from "@/components/common/TabBar";
import { PageContainer, PageHeader } from "@/components/common/PageContainer";
import { Badge } from "@/components/ui/Badge";
import { PendingApprovalsList } from "@/pages/approvals/PendingApprovalsList";
import { ApprovalHistory } from "@/pages/approvals/ApprovalHistory";
import { PermissionRulesEditor } from "@/pages/approvals/PermissionRulesEditor";
import { ApprovalStatsStrip } from "@/pages/approvals/ApprovalStatsStrip";

type ApprovalTab = "pending" | "history" | "rules";

/** 批准管理 — 待处理 / 历史 / 权限规则，顶部常驻统计条。 */
export default function Approvals() {
  const { t } = useTranslation("approvals");
  const [activeTab, setActiveTab] = useState<ApprovalTab>("pending");

  const { data: pendingData } = useQuery({
    queryKey: ["approvals", "pending"],
    queryFn: () => approvalsApi.pending().then((r) => r.data),
    refetchInterval: 3000,
  });
  const pendingCount = pendingData?.pending?.length ?? 0;

  const tabs: TabItem<ApprovalTab>[] = [
    {
      key: "pending",
      label: pendingCount > 0 ? `${t("tabs.pending")} (${pendingCount})` : t("tabs.pending"),
      icon: Clock,
    },
    { key: "history", label: t("tabs.history"), icon: History },
    { key: "rules", label: t("tabs.rules"), icon: Settings },
  ];

  return (
    <PageContainer wide>
      <PageHeader
        icon={<Shield size={20} className="text-accent" />}
        title={t("pageTitle")}
        subtitle={t("pageSubtitle")}
        actions={
          pendingCount > 0 ? (
            <Badge variant="warn" className="text-xs px-2.5 py-1">
              {t("pendingBadge", { count: pendingCount })}
            </Badge>
          ) : undefined
        }
      />

      <ApprovalStatsStrip />

      <TabBar tabs={tabs} activeTab={activeTab} onChange={setActiveTab} />

      {activeTab === "pending" && <PendingApprovalsList />}
      {activeTab === "history" && <ApprovalHistory />}
      {activeTab === "rules" && <PermissionRulesEditor />}
    </PageContainer>
  );
}
