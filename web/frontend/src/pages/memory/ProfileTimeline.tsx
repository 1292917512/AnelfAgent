import { useState } from "react";
import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { memoryApi } from "@/lib/api";
import { apiErrorMessage } from "@/lib/api/client";
import { Card } from "@/components/common/Card";
import { TabBar } from "@/components/common/TabBar";
import { DiffView } from "@/components/common/DiffView";
import type { ProfileOperation } from "@/lib/types";

function OperationDetail({ id }: { id: string }) {
  const { t } = useTranslation("memory");
  const [view, setView] = useState("diff");
  const { data, isPending, error } = useQuery({
    queryKey: ["profileOperation", id], queryFn: () => memoryApi.entities.operation(id).then(r => r.data),
  });
  if (isPending) return <p>{t("common:loading")}</p>;
  if (error) return <p role="alert">{apiErrorMessage(error, t("timelineLoadFailed"))}</p>;
  if (!data) return null;
  return <div className="space-y-2 mt-2">
    <TabBar tabs={[{ key: "diff", label: t("operationDiff") }, { key: "before", label: t("operationBefore") },
      { key: "after", label: t("operationAfter") }]} activeTab={view} onChange={setView} />
    <div className="max-h-96 overflow-auto">
      {view === "diff" ? <DiffView path={`${data.scope_type}:${data.scope_id}`} diff={data.diff}
        additions={data.diff.split("\n").filter(line => line.startsWith("+") && !line.startsWith("+++")).length}
        removals={data.diff.split("\n").filter(line => line.startsWith("-") && !line.startsWith("---")).length} /> :
        <pre className="text-sm whitespace-pre-wrap break-words">{view === "before" ? data.before_content : data.after_content}</pre>}
    </div>
  </div>;
}

function Operation({ item }: { item: ProfileOperation }) {
  const { t } = useTranslation("memory");
  const [open, setOpen] = useState(false);
  const qc = useQueryClient();
  const sync = useMutation({
    mutationFn: () => memoryApi.entities.sync(item.operation_id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["profileTimeline"] });
      qc.invalidateQueries({ queryKey: ["profileOperation", item.operation_id] });
      qc.invalidateQueries({ queryKey: ["ltmPaginated"] });
      qc.invalidateQueries({ queryKey: ["ltmSearch"] });
      qc.invalidateQueries({ queryKey: ["memoryTimeline"] });
    },
  });
  return <li className="min-w-0 break-words border-l-2 border-border pl-3 py-2 space-y-1">
    <div className="text-xs text-muted flex flex-wrap gap-2">
      <time>{new Date(item.created_at * 1000).toLocaleString()}</time>
      <span>{item.actor}</span><span>{item.scope_type}:{item.scope_id}</span>
      <span>{t(`operationActions.${item.action}`, { defaultValue: item.action })}</span>
      <span className={item.synced ? "text-ok" : "text-warning"}>{t(item.synced ? "operationSynced" : "operationPending")}</span>
    </div>
    {item.reason && <p className="text-sm break-words">{item.reason}</p>}
    {item.source_memory_id && <p className="text-xs text-muted">{t("operationSource")}: mem:{item.source_memory_id}</p>}
    {item.error && <p className="text-xs text-danger break-words">{item.error}</p>}
    {!item.synced && <button className="text-xs text-accent mr-3" disabled={sync.isPending} onClick={() => sync.mutate()}>{t("operationRetry")}</button>}
    {sync.error && <p role="alert" className="text-xs text-danger">{apiErrorMessage(sync.error, t("operationRetryFailed"))}</p>}
    <button className="text-xs text-accent" aria-expanded={open} onClick={() => setOpen(!open)}>{t(open ? "operationHide" : "operationDetails")}</button>
    {open && <OperationDetail id={item.operation_id} />}
  </li>;
}

export function ProfileTimeline({ scopeType = "", scopeId = "" }: { scopeType?: string; scopeId?: string }) {
  const { t } = useTranslation("memory");
  const query = useInfiniteQuery({
    queryKey: ["profileTimeline", scopeType, scopeId], initialPageParam: 0,
    queryFn: ({ pageParam }) => memoryApi.entities.timeline(scopeType, scopeId, pageParam).then(r => r.data),
    getNextPageParam: page => page.next_cursor ?? undefined,
  });
  const items = query.data?.pages.flatMap(page => page.items) ?? [];
  return <Card title={t("profileTimeline")} subtitle={t("profileTimelineHint")} actions={
    <button className="text-xs text-accent" onClick={() => query.refetch()}>{t("common:refresh")}</button>
  }>
    {query.isPending && <p>{t("common:loading")}</p>}
    {query.error && <p role="alert">{apiErrorMessage(query.error, t("timelineLoadFailed"))}</p>}
    {!query.isPending && !query.error && items.length === 0 && <p className="text-sm text-muted">{t("timelineEmpty")}</p>}
    <ol className="space-y-2">{items.map(item => <Operation key={item.id} item={item} />)}</ol>
    {query.hasNextPage && <button disabled={query.isFetchingNextPage} className="text-xs text-accent mt-3" onClick={() => query.fetchNextPage()}>{t("operationMore")}</button>}
  </Card>;
}
