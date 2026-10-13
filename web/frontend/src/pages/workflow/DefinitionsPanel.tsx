import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Pencil, Play, Plus, Trash2, Workflow } from "lucide-react";
import { workflowApi } from "@/lib/api";
import type { WorkflowDefinition, WorkflowRun } from "@/lib/types";
import { Button, EmptyState } from "@/components/ui";
import { ConfirmDialog } from "@/components/ui/Modal";
import { AsyncState, QueryError } from "@/components/common/AsyncState";
import { cn } from "@/lib/utils";
import { WorkflowComposer } from "./WorkflowComposer";

export function DefinitionsPanel({ onStarted }: { onStarted: (run: WorkflowRun) => void }) {
  const { t, i18n } = useTranslation("workflow");
  const client = useQueryClient();
  const [page, setPage] = useState(0);
  const [selected, setSelected] = useState<string | null>(null);
  const [editing, setEditing] = useState<{ definition: WorkflowDefinition | null } | null>(null);
  const [deleting, setDeleting] = useState(false);
  const query = useQuery({ queryKey: ["workflowDefinitions", page],
    queryFn: () => workflowApi.listDefinitions(30, page * 30).then((r) => r.data.definitions), refetchInterval: 4000, throwOnError: false });
  const detail = useQuery({ queryKey: ["workflowDefinition", selected], enabled: !!selected,
    queryFn: () => { if (!selected) throw new Error("Missing definition"); return workflowApi.definitionDetail(selected).then((r) => r.data); },
    refetchInterval: 4000, throwOnError: false });
  const refresh = () => {
    void client.invalidateQueries({ queryKey: ["workflowDefinitions"] });
    void client.invalidateQueries({ queryKey: ["workflowDefinition"] });
  };
  const start = useMutation({ mutationFn: (id: string) => workflowApi.startDefinition(id).then((r) => r.data),
    onSuccess: (run) => { void client.invalidateQueries({ queryKey: ["workflowRuns"] }); onStarted(run); } });
  const remove = useMutation({ mutationFn: (id: string) => workflowApi.deleteDefinition(id), onSuccess: () => {
    setDeleting(false); setSelected(null); client.removeQueries({ queryKey: ["workflowDefinition", selected] }); refresh();
  } });
  const definitions = query.data ?? [];
  return <div className="space-y-4">
    <div className="flex flex-wrap items-center justify-between gap-3">
      <p className="text-sm text-muted">{t("definitions.help")}</p>
      <Button variant="primary" onClick={() => setEditing({ definition: null })}><Plus size={16} />{t("definitions.create")}</Button>
    </div>
    <div className="grid items-start gap-5 xl:grid-cols-[19rem_minmax(0,1fr)]">
      <aside className="min-w-0 space-y-2">
        <AsyncState pending={query.isPending} error={query.error} retry={() => void query.refetch()}>
          {!definitions.length && <EmptyState icon={Workflow} title={t("definitions.empty")} />}
          {definitions.map((item) => <button key={item.workflow_id} onClick={() => { setSelected(item.workflow_id); start.reset(); remove.reset(); }}
            aria-pressed={selected === item.workflow_id}
            className={cn("w-full rounded-xl border bg-card p-4 text-left", selected === item.workflow_id ? "border-accent bg-accent/5" : "border-border hover:border-border-strong")}>
            <p className="truncate text-sm font-medium">{item.name}</p>
            <p className="mt-2 text-xs text-muted">{new Date(item.updated_at * 1000).toLocaleString(i18n.language)}</p>
          </button>)}
        </AsyncState>
        <div className="flex items-center justify-between gap-2">
          <Button size="sm" disabled={page === 0 || query.isFetching} onClick={() => setPage(page - 1)}>{t("list.previous")}</Button>
          <span className="text-xs text-muted">{t("list.page", { page: page + 1 })}</span>
          <Button size="sm" disabled={definitions.length < 30 || query.isFetching} onClick={() => setPage(page + 1)}>{t("list.next")}</Button>
        </div>
      </aside>
      <section className="min-w-0 space-y-4">
        {!selected ? <EmptyState icon={Workflow} title={t("definitions.select")} /> : <AsyncState pending={detail.isPending} error={detail.error} retry={() => void detail.refetch()}>
          {detail.data && <div className="space-y-4 rounded-xl border border-border bg-card p-4">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div><h2 className="font-semibold">{detail.data.name}</h2><p className="mt-1 font-mono text-xs text-muted">{detail.data.workflow_id}</p></div>
              <div className="flex flex-wrap gap-2">
                <Button size="sm" variant="primary" loading={start.isPending} disabled={remove.isPending} onClick={() => start.mutate(selected)}><Play size={14} />{t("start.submit")}</Button>
                <Button size="sm" disabled={start.isPending || remove.isPending} onClick={() => setEditing({ definition: detail.data! })}><Pencil size={14} />{t("common:edit")}</Button>
                <Button size="sm" variant="danger" disabled={start.isPending || remove.isPending} onClick={() => { remove.reset(); setDeleting(true); }}><Trash2 size={14} />{t("common:delete")}</Button>
              </div>
            </div>
            <p className="text-sm text-muted">{detail.data.spec.description}</p>
            <pre className="max-h-[32rem] overflow-auto whitespace-pre-wrap break-words rounded-lg bg-elevated p-3 text-xs">{JSON.stringify(detail.data.spec, null, 2)}</pre>
          </div>}
        </AsyncState>}
        {start.error && <QueryError compact error={start.error} />}
      </section>
    </div>
    {editing && <WorkflowComposer mode="definition" definition={editing.definition} onClose={() => setEditing(null)} onSaved={(definition) => {
      setEditing(null); setSelected(definition.workflow_id); setPage(0); refresh();
    }} />}
    <ConfirmDialog open={deleting} onClose={() => setDeleting(false)} onConfirm={() => { if (selected) remove.mutate(selected); }}
      title={t("definitions.deleteTitle")} danger loading={remove.isPending}
      message={<>{t("definitions.deleteMessage", { name: detail.data?.name })}{remove.error && <QueryError compact error={remove.error} />}</>} />
  </div>;
}
