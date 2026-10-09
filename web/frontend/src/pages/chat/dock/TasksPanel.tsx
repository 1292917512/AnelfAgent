import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { FolderOpen, FolderPlus, Pencil, Play, Plus } from "lucide-react";
import { tasksApi } from "@/lib/api";
import type { TaskConfig } from "@/lib/types";
import { Button, Switch } from "@/components/ui";
import { TaskEditor } from "@/pages/tasks/TaskEditor";

/** 任务面板：按文件夹分组 + 启停/触发/编辑/新建 */
export function DockTasksPanel() {
  const { t } = useTranslation("workbench");
  const queryClient = useQueryClient();
  const [editorOpen, setEditorOpen] = useState(false);
  const [editing, setEditing] = useState<TaskConfig | null>(null);

  const { data: tasks } = useQuery({
    queryKey: ["tasks"],
    queryFn: () => tasksApi.list().then((r) => r.data),
  });

  const invalidate = () => queryClient.invalidateQueries({ queryKey: ["tasks"] });

  const toggleMut = useMutation({
    mutationFn: (task: TaskConfig) => tasksApi.update(task.name, { enabled: !task.enabled }, task.folder || ""),
    onSuccess: invalidate,
  });
  const triggerMut = useMutation({
    mutationFn: (task: TaskConfig) => tasksApi.trigger(task.name, task.folder || ""),
  });
  /** 按文件夹分组 */
  const grouped = useMemo(() => {
    const map = new Map<string, TaskConfig[]>();
    for (const task of tasks ?? []) {
      const folder = task.folder || "";
      if (!map.has(folder)) map.set(folder, []);
      map.get(folder)!.push(task);
    }
    return [...map.entries()].sort(([a], [b]) => a.localeCompare(b));
  }, [tasks]);

  const openCreate = () => {
    setEditing(null);
    setEditorOpen(true);
  };
  const openEdit = (task: TaskConfig) => {
    setEditing(task);
    setEditorOpen(true);
  };

  return (
    <div className="flex flex-col h-full">
      <div className="flex items-center justify-between px-3 py-2 border-b border-border shrink-0">
        <span className="text-[11px] text-muted">{t("tasks.count", { count: tasks?.length ?? 0 })}</span>
        <Button variant="ghost" size="sm" onClick={openCreate}>
          <Plus size={13} /> {t("tasks.create")}
        </Button>
      </div>

      <div className="flex-1 overflow-y-auto p-2 space-y-3">
        {grouped.map(([folder, folderTasks]) => (
          <section key={folder || "(root)"}>
            <div className="flex items-center gap-1.5 px-1.5 py-1 text-[11px] font-medium text-muted">
              {folder ? <FolderOpen size={12} /> : <FolderPlus size={12} className="opacity-0" />}
              {folder || t("tasks.rootFolder")}
            </div>
            <div className="space-y-1">
              {folderTasks.map((task) => (
                <div
                  key={`${task.folder}/${task.name}`}
                  className="flex items-center gap-2 rounded-md border border-border bg-card px-2.5 py-2"
                >
                  <div className="flex-1 min-w-0">
                    <div className="text-xs font-medium text-heading truncate">
                      {task.display_name || task.name}
                    </div>
                    {task.description && (
                      <div className="text-[10px] text-muted truncate">{task.description}</div>
                    )}
                  </div>
                  <button
                    onClick={() => triggerMut.mutate(task)}
                    title={t("tasks.trigger")}
                    className="p-1 rounded text-muted hover:text-accent transition-colors shrink-0"
                  >
                    <Play size={13} />
                  </button>
                  <button
                    onClick={() => openEdit(task)}
                    title={t("tasks.edit")}
                    className="p-1 rounded text-muted hover:text-foreground transition-colors shrink-0"
                  >
                    <Pencil size={13} />
                  </button>
                  <Switch
                    label={t("tasks.edit")}
                    disabled={toggleMut.isPending}
                    checked={task.enabled}
                    onChange={() => toggleMut.mutate(task)}
                  />
                </div>
              ))}
            </div>
          </section>
        ))}
        {tasks && tasks.length === 0 && (
          <p className="text-xs text-muted text-center py-6">{t("tasks.empty")}</p>
        )}
      </div>

      {editorOpen && <TaskEditor task={editing} onClose={() => setEditorOpen(false)} />}
    </div>
  );
}
