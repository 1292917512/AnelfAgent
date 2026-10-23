import { useTranslation } from "react-i18next";
import type { CogneeStatus } from "@/lib/types";
import { cn } from "@/lib/utils";

/** 队列等待数扣除在途任务；映射数量独立统计，不与队列相加作为完成率。 */
export function SyncQueueSummary({ sync }: { sync: CogneeStatus["sync"] }) {
  const { t } = useTranslation("memory");
  const processing = sync.processing;
  const queued = typeof processing === "number" ? Math.max(0, sync.pending - processing) : undefined;
  const metrics = [
    { key: "pending", value: queued, tone: queued ? "text-warn" : "text-heading" },
    { key: "processing", value: processing, tone: processing ? "text-accent" : "text-heading" },
    { key: "failed", value: sync.failed, tone: sync.failed ? "text-danger" : "text-heading" },
    { key: "synced", value: sync.synced, tone: "text-heading" },
  ];
  return <div className="space-y-2" role="group" aria-label={t("cognee.queue.title")}>
    <dl className="grid grid-cols-2 gap-3 lg:grid-cols-4">
      {metrics.map(({ key, value, tone }) => <div key={key} className="min-w-0 rounded-xl border border-border bg-elevated/40 p-3 sm:p-4">
        <dt className="text-xs font-medium text-muted">{t(`cognee.queue.${key}`)}</dt>
        <dd className={cn("mt-2 break-words text-2xl font-semibold tabular-nums", tone)}>
          {typeof value === "number" ? value.toLocaleString() : "—"}
        </dd>
        <dd className="mt-1 text-xs leading-relaxed text-muted">{t(`cognee.queue.${key}Hint`)}</dd>
      </div>)}
    </dl>
    <p className="text-xs text-muted">{t("cognee.queue.countHint")}</p>
  </div>;
}
