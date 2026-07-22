import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { FolderOpen, FolderPlus, Pencil, Play, Plus } from "lucide-react";
import { tasksApi, type TaskConfig } from "@/lib/api";
import { Button, Input, Select, Switch, Textarea } from "@/components/ui";
import { Drawer } from "@/components/common/Drawer";

interface TaskFormState {
  name: string;
  display_name: string;
  folder: string;
  description: string;
  scope: string;
  prompt: string;
  enabled: boolean;
  importance: number;
}

const EMPTY_FORM: TaskFormState = {
  name: "",
  display_name: "",
  folder: "",
  description: "",
  scope: "global",
  prompt: "",
  enabled: true,
  importance: 0.5,
};

function taskToForm(task: TaskConfig): TaskFormState {
  return {
    name: task.name,
    display_name: task.display_name || "",
    folder: task.folder || "",
    description: task.description || "",
    scope: task.scope || "global",
    prompt: task.prompt || "",
    enabled: task.enabled,
    importance: task.importance ?? 0.5,
  };
}

/** 任务面板：按文件夹分组 + 启停/触发/编辑/新建 */
export function TasksPanel() {
  const { t } = useTranslation("workbench");
  const queryClient = useQueryClient();
  const [editorOpen, setEditorOpen] = useState(false);
  const [editing, setEditing] = useState<TaskConfig | null>(null);
  const [form, setForm] = useState<TaskFormState>(EMPTY_FORM);

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
  const saveMut = useMutation({
    mutationFn: async () => {
      const payload: TaskConfig = {
        ...(editing ?? ({} as TaskConfig)),
        name: form.name.trim(),
        display_name: form.display_name.trim() || form.name.trim(),
        description: form.description,
        scope: form.scope,
        enabled: form.enabled,
        memory_type: editing?.memory_type || "semantic",
        importance: form.importance,
        tags: editing?.tags || [],
        source: editing?.source || form.name.trim(),
        null_keywords: editing?.null_keywords || [],
        tool_tags: editing?.tool_tags || [],
        prompt: form.prompt,
        folder: form.folder.trim().replace(/^\/+|\/+$/g, ""),
      };
      if (editing) {
        return tasksApi.update(editing.name, payload, editing.folder || "");
      }
      return tasksApi.create(payload);
    },
    onSuccess: () => {
      invalidate();
      setEditorOpen(false);
    },
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
    setForm(EMPTY_FORM);
    setEditorOpen(true);
  };
  const openEdit = (task: TaskConfig) => {
    setEditing(task);
    setForm(taskToForm(task));
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

      {/* 编辑/新建抽屉 */}
      <Drawer
        open={editorOpen}
        onClose={() => setEditorOpen(false)}
        title={editing ? t("tasks.editTitle", { name: editing.name }) : t("tasks.createTitle")}
        footer={
          <>
            <Button variant="secondary" size="sm" onClick={() => setEditorOpen(false)}>
              {t("tasks.cancel")}
            </Button>
            <Button
              variant="primary"
              size="sm"
              onClick={() => saveMut.mutate()}
              disabled={saveMut.isPending || !form.name.trim() || !form.prompt.trim()}
            >
              {t("tasks.save")}
            </Button>
          </>
        }
      >
        <div className="space-y-3">
          <label className="block space-y-1">
            <span className="text-xs text-muted">{t("tasks.fieldName")}</span>
            <Input
              value={form.name}
              disabled={!!editing}
              onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
            />
          </label>
          <label className="block space-y-1">
            <span className="text-xs text-muted">{t("tasks.fieldDisplayName")}</span>
            <Input
              value={form.display_name}
              onChange={(e) => setForm((f) => ({ ...f, display_name: e.target.value }))}
            />
          </label>
          <label className="block space-y-1">
            <span className="text-xs text-muted">{t("tasks.fieldFolder")}</span>
            <Input
              value={form.folder}
              placeholder="dev/backend"
              onChange={(e) => setForm((f) => ({ ...f, folder: e.target.value }))}
            />
          </label>
          <label className="block space-y-1">
            <span className="text-xs text-muted">{t("tasks.fieldDescription")}</span>
            <Input
              value={form.description}
              onChange={(e) => setForm((f) => ({ ...f, description: e.target.value }))}
            />
          </label>
          <label className="block space-y-1">
            <span className="text-xs text-muted">{t("tasks.fieldScope")}</span>
            <Select
              value={form.scope}
              onChange={(e) => setForm((f) => ({ ...f, scope: e.target.value }))}
            >
              <option value="global">global</option>
              <option value="entity">entity</option>
              <option value="any">any</option>
            </Select>
          </label>
          <label className="block space-y-1">
            <span className="text-xs text-muted">{t("tasks.fieldPrompt")}</span>
            <Textarea
              rows={8}
              value={form.prompt}
              onChange={(e) => setForm((f) => ({ ...f, prompt: e.target.value }))}
            />
          </label>
          <label className="flex items-center gap-2">
            <Switch
              checked={form.enabled}
              onChange={(v) => setForm((f) => ({ ...f, enabled: v }))}
            />
            <span className="text-xs text-muted">{t("tasks.fieldEnabled")}</span>
          </label>
          {saveMut.isError && (
            <p className="text-[11px] text-danger">{t("tasks.saveFailed")}</p>
          )}
        </div>
      </Drawer>
    </div>
  );
}
