import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Play, Trash2, Pencil, Plus, ChevronDown } from "lucide-react";
import { tasksApi } from "@/lib/api";
import type { TaskConfig } from "@/lib/types";
import { cn } from "@/lib/utils";
import { Button, ConfirmDialog, Select } from "@/components/ui";
import { ListToolbar } from "@/components/common/ListToolbar";
import { AsyncState } from "@/components/common/AsyncState";
import { TaskDetail } from "./TaskForm";
import { TaskEditor, taskIdentity } from "./TaskEditor";
import { LastRunLine, TaskHistoryList } from "./TaskHistory";

export function TasksPanel() {
  const { t } = useTranslation(["appconfig", "common"]);
  const client = useQueryClient();
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState("all");
  const [editor, setEditor] = useState<{ task: TaskConfig | null } | null>(null);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [deleting, setDeleting] = useState<TaskConfig | null>(null);
  const tasks = useQuery({ queryKey: ["tasks"], queryFn: () => tasksApi.list().then((r) => r.data), refetchInterval: 15000, throwOnError: false });
  const remove = useMutation({
    mutationFn: (task: TaskConfig) => tasksApi.delete(task.name, task.folder ?? ""),
    onSuccess: () => { void client.invalidateQueries({ queryKey: ["tasks"] }); setDeleting(null); },
  });
  const trigger = useMutation({
    mutationFn: (task: TaskConfig) => tasksApi.trigger(task.name, task.folder ?? ""),
    onSuccess: () => { void client.invalidateQueries({ queryKey: ["tasks"] }); void client.invalidateQueries({ queryKey: ["task-history"] }); },
  });
  const matches = (tasks.data ?? []).filter((task) =>
    [task.name, task.display_name, task.description, task.folder].join(" ").toLowerCase().includes(search.trim().toLowerCase())
    && (status === "all" || task.enabled === (status === "enabled")));
  return <div className="space-y-4">
    <ListToolbar search={search} onSearch={setSearch} count={matches.length}>
      <Select aria-label={t("common:status")} value={status} onChange={(event) => setStatus(event.target.value)}>
        <option value="all">{t("common:all")}</option><option value="enabled">{t("tasks.enableTask")}</option><option value="disabled">{t("tasks.disabled")}</option>
      </Select>
      <Button variant="primary" onClick={() => setEditor({ task: null })}><Plus size={15} />{t("tasks.addTask")}</Button>
    </ListToolbar>
    <AsyncState pending={tasks.isPending} error={!tasks.data ? tasks.error : undefined} retry={() => void tasks.refetch()}>
      {matches.length === 0 && <p className="rounded-xl border border-dashed border-border py-16 text-center text-muted">{t(search || status !== "all" ? "common:noMatches" : "tasks.empty")}</p>}
      <div className="space-y-3">
        {matches.map((task) => {
          const key = taskIdentity(task);
          const open = key === expanded;
          return <section key={key} className="overflow-hidden rounded-xl border border-border bg-card">
            <div className="flex flex-wrap items-center gap-3 p-4">
              <button aria-expanded={open} onClick={() => setExpanded(open ? null : key)} className="flex min-w-0 flex-1 items-start gap-3 text-left">
                <ChevronDown size={16} className={cn("mt-1 shrink-0 text-muted transition-transform", open && "rotate-180")} />
                <div className="min-w-0">
                  <span className="flex flex-wrap items-center gap-2 text-sm font-semibold text-heading">{task.display_name || task.name}
                    <span className={cn("h-1.5 w-1.5 rounded-full", task.enabled ? "bg-ok" : "bg-muted")} />
                    {!task.enabled && <span className="text-xs font-normal text-muted">{t("tasks.disabled")}</span>}
                  </span>
                  <span className="block truncate text-xs text-muted">{task.folder ? task.folder + "/" : ""}{task.name}</span>
                  {task.description && <p className="mt-1 text-xs text-muted">{task.description}</p>}
                  {task.last_run && <LastRunLine lastRun={task.last_run} t={t} />}
                </div>
              </button>
              <div className="ml-auto flex items-center gap-1">
                <Button size="sm" loading={trigger.isPending && taskIdentity(trigger.variables) === key}
                  disabled={!task.enabled || trigger.isPending} onClick={() => trigger.mutate(task)}><Play size={13} />{t("tasks.execute")}</Button>
                <Button size="icon" variant="ghost" title={t("common:edit")} onClick={() => setEditor({ task })}><Pencil size={14} /></Button>
                <Button size="icon" variant="ghost" title={t("common:delete")} onClick={() => setDeleting(task)}><Trash2 size={14} /></Button>
              </div>
            </div>
            {open && <div className="space-y-4 border-t border-border p-4"><TaskDetail task={task} /><TaskHistoryList name={task.name} t={t} /></div>}
          </section>;
        })}
      </div>
    </AsyncState>
    {editor && <TaskEditor task={editor.task} onClose={() => setEditor(null)} />}
    <ConfirmDialog open={!!deleting} title={t("common:delete")} message={t("tasks.confirmDelete", { name: deleting?.display_name || deleting?.name || "" })}
      onClose={() => setDeleting(null)} onConfirm={() => { if (deleting) remove.mutate(deleting); }} danger loading={remove.isPending} />
  </div>;
}
