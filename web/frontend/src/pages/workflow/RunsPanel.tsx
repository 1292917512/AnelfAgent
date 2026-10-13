import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { Play, Plus, RotateCcw, Save, Square, Trash2, Workflow } from "lucide-react";
import { workflowApi, type WorkflowRun, type WorkflowRunDetail } from "@/lib/api";
import { Badge, Button, EmptyState } from "@/components/ui";
import { AsyncState, QueryError } from "@/components/common/AsyncState";
import { ConfirmDialog } from "@/components/ui/Modal";
import { cn } from "@/lib/utils";
import { RunDetail } from "./RunDetail";
import { WorkflowComposer } from "./WorkflowComposer";
import { CleanupDialog } from "./CleanupDialog";

export function RunsPanel() {
  const { t, i18n } = useTranslation("workflow");
  const client = useQueryClient();
  const [params, setParams] = useSearchParams();
  const selected = params.get("run");
  const [composer, setComposer] = useState<{ source: WorkflowRunDetail | null } | null>(null);
  const [saveSource, setSaveSource] = useState<WorkflowRunDetail | null>(null);
  const [page, setPage] = useState(0);
  const [deleting, setDeleting] = useState(false);
  const [cleaning, setCleaning] = useState(false);
  const [notice, setNotice] = useState("");
  const runsQuery = useQuery({
    queryKey: ["workflowRuns", page], queryFn: () => workflowApi.listRuns(30, page * 30).then((r) => r.data.runs),
    refetchInterval: 4000, throwOnError: false,
  });
  const runs = runsQuery.data ?? [];
  const listedRun = runs.find((run) => run.run_id === selected);
  const detailQuery = useQuery({
    queryKey: ["workflowRunDetail", selected],
    queryFn: () => { if (!selected) throw new Error("Missing run"); return workflowApi.runDetail(selected).then((r) => r.data); },
    enabled: !!selected, throwOnError: false,
    refetchInterval: (query) => listedRun?.running || query.state.data?.run.running ? 4000 : false,
  });
  const previous = useRef<{ id: string; running: boolean } | null>(null);
  useEffect(() => {
    if (!listedRun) { previous.current = null; return; }
    if (previous.current?.id === listedRun.run_id && previous.current.running && !listedRun.running) {
      void client.invalidateQueries({ queryKey: ["workflowRunDetail", listedRun.run_id] });
    }
    previous.current = { id: listedRun.run_id, running: !!listedRun.running };
  }, [listedRun, client]);
  const select = (id: string) => setParams((current) => { const next = new URLSearchParams(current); next.set("run", id); return next; });
  const invalidate = () => {
    void client.invalidateQueries({ queryKey: ["workflowRuns"] });
    void client.invalidateQueries({ queryKey: ["workflowRunDetail"] });
  };
  const action = useMutation({
    mutationFn: async ({ id, kind }: { id: string; kind: "stop" | "resume" }) => { if (kind === "stop") await workflowApi.stopRun(id); else await workflowApi.resumeRun(id); },
    onSuccess: invalidate,
  });
  const clearSelection = () => setParams((current) => {
    const next = new URLSearchParams(current); next.delete("run"); next.set("tab", "runs"); return next;
  });
  const remove = useMutation({ mutationFn: (id: string) => workflowApi.deleteRun(id), onSuccess: () => {
    setDeleting(false); clearSelection(); client.removeQueries({ queryKey: ["workflowRunDetail", selected] }); invalidate();
  } });
  const detail = detailQuery.data;
  const run = listedRun ?? detail?.run;
  const variant = (item: WorkflowRun) => item.status === "completed" ? "ok" : item.status === "failed" ? "danger" : item.running ? "info" : "neutral";
  return <div className="space-y-4">
    <div className="flex items-center justify-between gap-3">
      <p className="text-sm text-muted">{t("list.title")} · {runs.length}</p>
      <div className="flex flex-wrap gap-2">
        <Button onClick={() => setCleaning(true)}><Trash2 size={16} />{t("cleanup.title")}</Button>
        <Button variant="primary" onClick={() => setComposer({ source: null })}><Plus size={16} />{t("start.submit")}</Button>
      </div>
    </div>
    {notice && <p role="status" className="text-sm text-ok">{notice}</p>}
    <div className="grid items-start gap-5 xl:grid-cols-[19rem_minmax(0,1fr)]">
      <aside className="min-w-0 space-y-2 xl:sticky xl:top-4">
        <AsyncState pending={runsQuery.isPending} error={runsQuery.error} retry={() => void runsQuery.refetch()}>
          {!runs.length && <EmptyState icon={Workflow} title={t("list.empty")} />}
          {runs.map((item) => <button key={item.run_id} onClick={() => select(item.run_id)} aria-pressed={selected === item.run_id}
            className={cn("w-full rounded-xl border bg-card p-4 text-left transition-colors", selected === item.run_id ? "border-accent bg-accent/5" : "border-border hover:border-border-strong")}>
            <div className="mb-2 flex items-center justify-between gap-2"><span className="truncate text-sm font-medium">{item.name}</span>
              <Badge variant={variant(item)}>{t(`status.${item.status}`)}</Badge></div>
            <p className="truncate font-mono text-xs text-muted">{item.run_id}</p>
            {!!item.created_at && <p className="mt-2 text-xs text-muted">{new Date(item.created_at * 1000).toLocaleString(i18n.language)}</p>}
          </button>)}
        </AsyncState>
        <div className="flex items-center justify-between gap-2">
          <Button size="sm" disabled={page === 0 || runsQuery.isFetching} onClick={() => setPage(page - 1)}>{t("list.previous")}</Button>
          <span className="text-xs text-muted">{t("list.page", { page: page + 1 })}</span>
          <Button size="sm" disabled={runs.length < 30 || runsQuery.isFetching} onClick={() => setPage(page + 1)}>{t("list.next")}</Button>
        </div>
      </aside>
      <section className="min-w-0 space-y-4">
        {!selected ? <EmptyState icon={Workflow} title={t("detail.empty")} /> : <>
          {run && <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-border bg-card p-4">
            <div className="min-w-0"><h2 className="truncate font-semibold">{run.name}</h2>
              <p className="mt-1 break-all font-mono text-xs text-muted">{run.run_id}</p>
              {run.parent_run_id && <button className="mt-1 text-xs text-accent hover:underline" onClick={() => select(run.parent_run_id!)}>{t("detail.parent", { id: run.parent_run_id })}</button>}
            </div>
            <div className="flex flex-wrap gap-2">
              <Badge variant={variant(run)}>{t(`status.${run.status}`)}</Badge>
              {run.running ? <Button size="sm" onClick={() => action.mutate({ id: run.run_id, kind: "stop" })} loading={action.isPending}><Square size={13} />{t("action.stop")}</Button>
                : run.status === "stopped" ? <Button size="sm" variant="primary" onClick={() => action.mutate({ id: run.run_id, kind: "resume" })} loading={action.isPending}><RotateCcw size={13} />{t("action.resume")}</Button> : null}
              {!run.running && run.status !== "running" && detail && <Button size="sm" onClick={() => setComposer({ source: detail })}><Play size={13} />{t("action.revise")}</Button>}
              {detail && <Button size="sm" onClick={() => setSaveSource(detail)}><Save size={13} />{t("definitions.saveFromRun")}</Button>}
              {!run.running && run.status !== "running" && <Button size="sm" variant="danger" disabled={action.isPending} onClick={() => { remove.reset(); setDeleting(true); }}><Trash2 size={13} />{t("action.delete")}</Button>}
            </div>
          </div>}
          {action.error && <QueryError compact error={action.error} />}
          <AsyncState pending={detailQuery.isPending} error={detailQuery.error} retry={() => void detailQuery.refetch()}>
            {detail && <RunDetail detail={detail} />}
          </AsyncState>
        </>}
      </section>
    </div>
    {composer && <WorkflowComposer source={composer.source} onClose={() => setComposer(null)} onStarted={(started) => {
      setComposer(null); setPage(0); invalidate(); select(started.run_id);
    }} />}
    {saveSource && <WorkflowComposer mode="definition" definition={null} source={saveSource} onClose={() => setSaveSource(null)} onSaved={() => {
      setSaveSource(null); void client.invalidateQueries({ queryKey: ["workflowDefinitions"] }); setNotice(t("definitions.saved"));
    }} />}
    <ConfirmDialog open={deleting} onClose={() => setDeleting(false)} title={t("action.delete")} danger loading={remove.isPending}
      onConfirm={() => { if (selected) remove.mutate(selected); }}
      message={<>{t("cleanup.deleteMessage", { name: run?.name })}{remove.error && <QueryError compact error={remove.error} />}</>} />
    {cleaning && <CleanupDialog onClose={() => setCleaning(false)} onCleaned={(count) => {
      setCleaning(false); clearSelection(); setPage(0); client.removeQueries({ queryKey: ["workflowRunDetail"] }); invalidate(); setNotice(t("cleanup.done", { count }));
    }} />}
  </div>;
}
