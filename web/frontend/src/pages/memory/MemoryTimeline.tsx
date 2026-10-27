import { useInfiniteQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { memoryApi } from "@/lib/api";
import { apiErrorMessage } from "@/lib/api/client";
import { Card } from "@/components/common/Card";

export function MemoryTimeline() {
  const { t } = useTranslation("memory");
  const query = useInfiniteQuery({
    queryKey: ["memoryTimeline"], initialPageParam: 0,
    queryFn: ({ pageParam }) => memoryApi.timeline(0, pageParam).then(r => r.data),
    getNextPageParam: page => page.next_cursor ?? undefined,
  });
  const items = query.data?.pages.flatMap(page => page.items) ?? [];
  return <Card title={t("memoryTimeline")} subtitle={t("memoryTimelineHint", { days: query.data?.pages[0]?.retention_days ?? 30 })}
    actions={<button className="text-xs text-accent" onClick={() => query.refetch()}>{t("common:refresh")}</button>}>
    {query.isPending && <p>{t("common:loading")}</p>}
    {query.error && <p role="alert">{apiErrorMessage(query.error, t("timelineLoadFailed"))}</p>}
    {!query.isPending && !query.error && !items.length && <p className="text-sm text-muted">{t("timelineEmpty")}</p>}
    <ol className="space-y-3">{items.map(item => <li key={item.id} className="border-l-2 border-border pl-3 text-sm space-y-1">
      <div className="text-xs text-muted">{new Date(item.timestamp * 1000).toLocaleString()} · {item.actor} · mem:{item.memory_id} · {t(`operationActions.${item.action}`, { defaultValue: item.action })}</div>
      {(item.data.status_before || item.data.status_after) && <p>{t(`reflectionStates.${item.data.status_before}`, { defaultValue: item.data.status_before || "—" })} → {t(`reflectionStates.${item.data.status_after}`, { defaultValue: item.data.status_after || "—" })}</p>}
      <p className="break-words">{item.data.reason || item.detail}</p>
      {item.data.error && <p className="text-danger break-words">{item.data.error}</p>}
    </li>)}</ol>
    {query.hasNextPage && <button disabled={query.isFetchingNextPage} className="text-xs text-accent mt-3" onClick={() => query.fetchNextPage()}>{t("operationMore")}</button>}
  </Card>;
}
