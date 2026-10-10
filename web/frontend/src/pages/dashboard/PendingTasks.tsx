import { Inbox } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Card } from "@/components/common/Card";
import type { PfcSnapshot } from "@/lib/types";

export function PendingTasks({ pfc }: { pfc?: PfcSnapshot }) {
  const { t } = useTranslation("status");
  const items = [
    ...(pfc?.pending_messages ?? []).map((item) => ({ ...item, type: item.adapter_key || t("message") })),
    ...(pfc?.general_tasks ?? []),
  ];
  return <Card title={t("pendingTasks")} actions={<Inbox size={16} className="text-muted" />}>
    <div className="overview-pending">
      {items.map((item, index) => <div key={`${item.scope}-${index}`}>
        <span className="overview-pending-type">{item.type}</span>
        <div className="min-w-0"><p className="line-clamp-2 text-sm break-words">{item.preview}</p><span className="block truncate text-xs text-muted" title={item.scope}>{item.scope}</span></div>
      </div>)}
      {(pfc?.pending_analysis_count ?? 0) > 0 && <p className="text-xs text-muted">{t("entitiesWaiting", { count: pfc?.pending_analysis_count })}</p>}
      {!items.length && !pfc?.pending_analysis_count && <p className="text-sm text-muted">{pfc ? t("noPendingTasks") : t("common:loading")}</p>}
    </div>
  </Card>;
}
