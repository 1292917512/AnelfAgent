import { useTranslation } from "react-i18next";
import { useQuery } from "@tanstack/react-query";
import { approvalsApi } from "@/lib/api";
import { StatCard } from "@/components/common/StatCard";

export function ApprovalStatsStrip() {
  const { t } = useTranslation("approvals");
  const { data } = useQuery({
    queryKey: ["approvals", "stats"],
    queryFn: () => approvalsApi.stats().then((r) => r.data),
    refetchInterval: 10000,
  });
  const counts = data?.by_outcome;
  return (
    <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
      <StatCard label={t("stats.totalDecisions")} value={data ? String(data.total) : "—"} />
      <StatCard label={t("decision.guardian_approved")} value={counts ? String(counts.guardian_approved ?? 0) : "—"} />
      <StatCard label={t("stats.blocked")} value={counts ? String((counts.denied ?? 0) + (counts.guardian_denied ?? 0) + (counts.permission_error ?? 0)) : "—"} />
      <StatCard label={t("decision.guardian_bypass")} value={counts ? String(counts.guardian_bypass ?? 0) : "—"} variant={counts?.guardian_bypass ? "warn" : "default"} />
    </div>
  );
}
