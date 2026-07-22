import { useTranslation } from "react-i18next";
import { useQuery } from "@tanstack/react-query";
import { approvalsApi } from "@/lib/api";
import { StatCard } from "@/components/common/StatCard";
import { Card } from "@/components/common/Card";

type DecisionKey = "approved" | "denied" | "expired" | "cancelled";

const DECISION_ORDER: DecisionKey[] = ["approved", "denied", "expired", "cancelled"];

const DECISION_BAR: Record<DecisionKey, string> = {
  approved: "bg-ok",
  denied: "bg-danger",
  expired: "bg-warn",
  cancelled: "bg-muted-strong",
};

const DECISION_DOT: Record<DecisionKey, string> = {
  approved: "bg-ok",
  denied: "bg-danger",
  expired: "bg-warn",
  cancelled: "bg-muted-strong",
};

/** 批准管理页顶部常驻统计条：待处理 / 总决策 / 批准率 + 决策分布条 */
export function ApprovalStatsStrip() {
  const { t } = useTranslation("approvals");

  const { data } = useQuery({
    queryKey: ["approvals", "stats"],
    queryFn: () => approvalsApi.stats().then((r) => r.data),
    refetchInterval: 5000,
  });

  const pendingCount = (data?.pending_count as number) ?? 0;
  const byDecision = (data?.history_by_decision ?? {}) as Record<string, number>;
  const totalDecisions = DECISION_ORDER.reduce((sum, k) => sum + (byDecision[k] ?? 0), 0);
  const approvalRate = totalDecisions > 0 ? Math.round(((byDecision.approved ?? 0) / totalDecisions) * 100) : 0;

  return (
    <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
      <StatCard
        label={t("stats.pending")}
        value={String(pendingCount)}
        variant={pendingCount > 0 ? "warn" : "ok"}
      />
      <StatCard label={t("stats.totalDecisions")} value={String(totalDecisions)} />
      <StatCard label={t("stats.approvalRate")} value={`${approvalRate}%`} />
      <Card className="p-3 flex flex-col justify-center gap-2">
        <div className="text-[11px] font-medium uppercase tracking-wide text-muted">
          {t("stats.decisionBreakdown")}
        </div>
        {totalDecisions > 0 ? (
          <>
            <div className="flex h-2 rounded-full overflow-hidden bg-secondary">
              {DECISION_ORDER.map((k) =>
                (byDecision[k] ?? 0) > 0 ? (
                  <div
                    key={k}
                    className={DECISION_BAR[k]}
                    style={{ width: `${((byDecision[k] ?? 0) / totalDecisions) * 100}%` }}
                  />
                ) : null,
              )}
            </div>
            <div className="flex flex-wrap gap-x-3 gap-y-1">
              {DECISION_ORDER.map((k) => (
                <span key={k} className="flex items-center gap-1 text-[11px] text-muted">
                  <span className={`w-2 h-2 rounded-full ${DECISION_DOT[k]}`} />
                  {t(`decision.${k}`)} {byDecision[k] ?? 0}
                </span>
              ))}
            </div>
          </>
        ) : (
          <div className="text-xs text-muted">{t("noHistory")}</div>
        )}
      </Card>
    </div>
  );
}
