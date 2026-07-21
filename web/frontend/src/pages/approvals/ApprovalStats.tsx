import { useTranslation } from "react-i18next";
import { useQuery } from "@tanstack/react-query";
import { approvalsApi } from "@/lib/api";
import { Shield, CheckCircle, XCircle, Clock, Ban } from "lucide-react";

export function ApprovalStats() {
  const { t } = useTranslation("approvals");

  const { data, isLoading } = useQuery({
    queryKey: ["approvals", "stats"],
    queryFn: () => approvalsApi.stats().then((r) => r.data),
    refetchInterval: 5000,
  });

  if (isLoading) {
    return <div className="text-center py-8 text-muted-foreground">{t("loading")}</div>;
  }

  const stats = data || {};
  const historyByDecision = stats.history_by_decision || {};

  const totalDecisions = Object.values(historyByDecision).reduce(
    (sum: number, count) => sum + (count as number),
    0
  );

  return (
    <div className="space-y-6">
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <div className="border border-border rounded-lg p-6">
          <div className="flex items-center gap-3 mb-2">
            <Clock className="w-5 h-5 text-blue-500" />
            <h3 className="font-medium">{t("stats.pending")}</h3>
          </div>
          <div className="text-3xl font-bold">{stats.pending_count || 0}</div>
        </div>

        <div className="border border-border rounded-lg p-6">
          <div className="flex items-center gap-3 mb-2">
            <Shield className="w-5 h-5 text-green-500" />
            <h3 className="font-medium">{t("stats.totalDecisions")}</h3>
          </div>
          <div className="text-3xl font-bold">{totalDecisions}</div>
        </div>

        <div className="border border-border rounded-lg p-6">
          <div className="flex items-center gap-3 mb-2">
            <CheckCircle className="w-5 h-5 text-green-500" />
            <h3 className="font-medium">{t("stats.approvalRate")}</h3>
          </div>
          <div className="text-3xl font-bold">
            {totalDecisions > 0
              ? Math.round(((historyByDecision.approved || 0) / totalDecisions) * 100)
              : 0}
            %
          </div>
        </div>
      </div>

      <div className="border border-border rounded-lg p-6">
        <h3 className="font-medium mb-4">{t("stats.decisionBreakdown")}</h3>
        <div className="space-y-3">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <CheckCircle className="w-4 h-4 text-green-500" />
              <span>{t("decision.approved")}</span>
            </div>
            <span className="font-medium">{historyByDecision.approved || 0}</span>
          </div>

          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <XCircle className="w-4 h-4 text-red-500" />
              <span>{t("decision.denied")}</span>
            </div>
            <span className="font-medium">{historyByDecision.denied || 0}</span>
          </div>

          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <Clock className="w-4 h-4 text-yellow-500" />
              <span>{t("decision.expired")}</span>
            </div>
            <span className="font-medium">{historyByDecision.expired || 0}</span>
          </div>

          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <Ban className="w-4 h-4 text-gray-500" />
              <span>{t("decision.cancelled")}</span>
            </div>
            <span className="font-medium">{historyByDecision.cancelled || 0}</span>
          </div>
        </div>
      </div>
    </div>
  );
}
