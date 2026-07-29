import { useEffect, useRef, useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { tasksApi, type TaskConfig } from "@/lib/api";
import { Card } from "@/components/common/Card";
import { cn } from "@/lib/utils";
import { Play, Trash2, Pencil, Plus, Save, X, ChevronDown, ChevronUp } from "lucide-react";
import { TaskCreateForm, TaskDetail, TaskFormFields } from "./TaskForm";
import { Button, ConfirmDialog } from "@/components/ui";

export function TasksPanel() {
  const { t } = useTranslation("appconfig");
  const { t: tc } = useTranslation("common");
  const queryClient = useQueryClient();
  const [triggerStates, setTriggerStates] = useState<Record<string, "idle" | "pending" | "ok" | "error">>({});
  const [editingTask, setEditingTask] = useState<TaskConfig | null>(null);
  const [isCreating, setIsCreating] = useState(false);
  const [expandedName, setExpandedName] = useState<string | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<TaskConfig | null>(null);
  // 每个任务的触发反馈定时器：卸载时统一清理，避免 setState on unmounted
  const triggerTimersRef = useRef<Map<string, ReturnType<typeof setTimeout>>>(new Map());
  useEffect(() => {
    const timers = triggerTimersRef.current;
    return () => {
      for (const timer of timers.values()) clearTimeout(timer);
      timers.clear();
    };
  }, []);

  const { data: tasks = [] } = useQuery({
    queryKey: ["tasks"],
    queryFn: () => tasksApi.list().then((r) => r.data),
  });

  const invalidate = () => queryClient.invalidateQueries({ queryKey: ["tasks"] });

  const handleTrigger = async (name: string) => {
    setTriggerStates((s) => ({ ...s, [name]: "pending" }));
    const resetToIdle = () => {
      const prev = triggerTimersRef.current.get(name);
      if (prev) clearTimeout(prev);
      triggerTimersRef.current.set(
        name,
        setTimeout(() => {
          triggerTimersRef.current.delete(name);
          setTriggerStates((s) => ({ ...s, [name]: "idle" }));
        }, 3000),
      );
    };
    try {
      await tasksApi.trigger(name);
      setTriggerStates((s) => ({ ...s, [name]: "ok" }));
      resetToIdle();
    } catch {
      setTriggerStates((s) => ({ ...s, [name]: "error" }));
      resetToIdle();
    }
  };

  const createMut = useMutation({
    mutationFn: (data: TaskConfig) => tasksApi.create(data),
    onSuccess: () => { invalidate(); setIsCreating(false); },
  });

  const updateMut = useMutation({
    mutationFn: ({ name, data }: { name: string; data: Partial<TaskConfig> }) => tasksApi.update(name, data),
    onSuccess: () => { invalidate(); setEditingTask(null); },
  });

  const deleteMut = useMutation({
    mutationFn: (name: string) => tasksApi.delete(name),
    onSuccess: () => invalidate(),
  });

  const triggerLabel = (state: string) => {
    switch (state) {
      case "pending": return t("tasks.executing");
      case "ok": return t("tasks.triggered");
      case "error": return t("tasks.failed");
      default: return t("tasks.execute");
    }
  };

  return (
    <Card title={t("tasks.title")} subtitle={t("tasks.subtitle", { count: (tasks as TaskConfig[]).length })}>
      <div className="space-y-3">
        {(tasks as TaskConfig[]).length === 0 && !isCreating && (
          <p className="text-sm text-muted py-2">{t("tasks.empty")}</p>
        )}

        {(tasks as TaskConfig[]).map((task) => {
          const state = triggerStates[task.name] || "idle";
          const isExpanded = expandedName === task.name;
          const isEditing = editingTask?.name === task.name;

          return (
            <div key={task.name} className="border border-border rounded-md overflow-hidden">
              <div
                className="flex items-center gap-3 px-3 py-2.5 bg-elevated cursor-pointer hover:bg-hover transition-colors"
                onClick={() => setExpandedName(isExpanded ? null : task.name)}
              >
                <span className={cn("w-1.5 h-1.5 rounded-full flex-shrink-0", task.enabled ? "bg-ok" : "bg-muted")} />
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 flex-wrap">
                    <span className="text-sm font-medium text-heading">{task.display_name || task.name}</span>
                    <span className="text-xs text-muted">{task.name}</span>
                    {!task.enabled && (
                      <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-secondary text-muted">{t("tasks.disabled")}</span>
                    )}
                  </div>
                  {task.description && <p className="text-xs text-muted truncate mt-0.5">{task.description}</p>}
                </div>
                <div className="flex items-center gap-1 flex-shrink-0" onClick={(e) => e.stopPropagation()}>
                  <button
                    onClick={() => handleTrigger(task.name)}
                    disabled={state === "pending" || !task.enabled}
                    className={cn(
                      "flex items-center gap-1 px-2.5 py-1 text-xs font-medium rounded-md transition-all",
                      state === "ok"
                        ? "bg-ok-subtle text-ok"
                        : state === "error"
                          ? "bg-danger-subtle text-danger"
                          : "bg-accent text-primary-foreground hover:bg-accent-hover",
                      (state === "pending" || !task.enabled) && "opacity-50 cursor-not-allowed",
                    )}
                  >
                    <Play size={11} /> <span className="hidden sm:inline">{triggerLabel(state)}</span>
                  </button>
                  <button
                    onClick={() => { setEditingTask({ ...task }); setExpandedName(task.name); }}
                    className="p-1.5 rounded hover:bg-hover text-muted hover:text-accent transition-colors"
                    title={tc("edit")}
                  >
                    <Pencil size={13} />
                  </button>
                  <button
                    onClick={() => setDeleteTarget(task)}
                    className="p-1.5 rounded hover:bg-hover text-muted hover:text-danger transition-colors"
                    title={tc("delete")}
                  >
                    <Trash2 size={13} />
                  </button>
                </div>
                {isExpanded ? <ChevronUp size={14} className="text-muted" /> : <ChevronDown size={14} className="text-muted" />}
              </div>

              {isExpanded && (
                <div className="px-3 py-3 border-t border-border">
                  {isEditing ? (
                    <div className="space-y-3">
                      <TaskFormFields
                        task={editingTask!}
                        set={(key, value) => setEditingTask((prev) => prev ? { ...prev, [key]: value } : prev)}
                      />
                      <div className="flex items-center gap-2">
                        <Button variant="primary" size="sm" onClick={() => {
                          const { name, ...rest } = editingTask!;
                          updateMut.mutate({ name, data: rest });
                        }} loading={updateMut.isPending}>
                          <Save size={12} /> {updateMut.isPending ? t("actions.saving") : t("actions.save")}
                        </Button>
                        <Button variant="secondary" size="sm" onClick={() => setEditingTask(null)}>
                          <X size={12} /> {t("actions.cancel")}
                        </Button>
                      </div>
                    </div>
                  ) : (
                    <TaskDetail task={task} />
                  )}
                </div>
              )}
            </div>
          );
        })}

        {isCreating && (
          <TaskCreateForm
            onSave={(task) => createMut.mutate(task)}
            onCancel={() => setIsCreating(false)}
            isPending={createMut.isPending}
          />
        )}

        {!isCreating && (
          <button
            onClick={() => setIsCreating(true)}
            className="flex items-center gap-1.5 px-3 py-1.5 text-sm font-medium rounded-md border border-dashed border-border text-muted hover:border-accent hover:text-accent transition-colors"
          >
            <Plus size={14} /> {t("tasks.addTask")}
          </button>
        )}
      </div>
      <ConfirmDialog
        open={!!deleteTarget}
        onClose={() => setDeleteTarget(null)}
        onConfirm={() => {
          if (deleteTarget) deleteMut.mutate(deleteTarget.name);
          setDeleteTarget(null);
        }}
        title={tc("delete")}
        message={t("tasks.confirmDelete", { name: deleteTarget?.display_name || deleteTarget?.name || "" })}
        confirmText={deleteMut.isPending ? tc("saving") : tc("delete")}
        cancelText={tc("cancel")}
        danger
        loading={deleteMut.isPending}
      />
    </Card>
  );
}

