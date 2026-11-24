import { Inbox } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Card } from "@/components/common/Card";
import { QueryError } from "@/components/common/AsyncState";
import type { ThinkingSnapshot } from "@/lib/types";

export function PendingTasks({ snapshot, loading, error, retry }: { snapshot: ThinkingSnapshot | null; loading: boolean; error: Error | null; retry: () => void }) {
  const { t } = useTranslation("status");
  const items = [
    ...(snapshot?.pending_messages ?? []).map((item) => ({ ...item, type: item.adapter_key || t("message") })),
    ...(snapshot?.general_tasks ?? []),
  ];
  return <Card title={t("pendingTasks")} actions={<Inbox size={16} className="text-muted" />}>
    <div className="overview-pending">
      {items.map((item, index) => <div key={`${item.scope}-${index}`}>
        <span className="overview-pending-type">{item.type}</span>
        <div className="min-w-0"><p className="line-clamp-2 text-sm break-words">{item.preview}</p><span className="block truncate text-xs text-muted" title={item.scope}>{item.scope}</span></div>
      </div>)}
      {(snapshot?.pending_analysis_count ?? 0) > 0 && <p className="text-xs text-muted">{t("entitiesWaiting", { count: snapshot?.pending_analysis_count })}</p>}
      {error ? <QueryError compact error={error} retry={retry} /> : !items.length && !snapshot?.pending_analysis_count &&
        <p className="text-sm text-muted">{loading ? t("common:loading") : snapshot?.pending_messages && snapshot.general_tasks ? t("noPendingTasks") : t("common:unavailable")}</p>}
    </div>
  </Card>;
}
