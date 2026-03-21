import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { tasksApi, modelsApi, type ReasoningEffort, type TaskConfig } from "@/lib/api";
import { Card } from "@/components/common/Card";
import { cn } from "@/lib/utils";
import { Play, Trash2, Pencil, Plus, Save, X, ChevronDown, ChevronUp } from "lucide-react";

const EMPTY_TASK: TaskConfig = {
  name: "",
  display_name: "",
  description: "",
  model_id: null,
  scope: "global",
  enabled: true,
  memory_type: "semantic",
  importance: 0.5,
  tags: [],
  source: "",
  null_keywords: [],
  tool_tags: [],
  prompt: "",
  allow_output_tools: false,
  save_result_to_memory: true,
  reasoning_effort: null,
};

const REASONING_EFFORTS: Array<{ value: ReasoningEffort; key: string }> = [
  { value: "low", key: "tasks.effortLow" },
  { value: "medium", key: "tasks.effortMedium" },
  { value: "high", key: "tasks.effortHigh" },
  { value: "max", key: "tasks.effortMax" },
];

export function TasksPanel() {
  const { t } = useTranslation("appconfig");
  const { t: tc } = useTranslation("common");
  const queryClient = useQueryClient();
  const [triggerStates, setTriggerStates] = useState<Record<string, "idle" | "pending" | "ok" | "error">>({});
  const [editingTask, setEditingTask] = useState<TaskConfig | null>(null);
  const [isCreating, setIsCreating] = useState(false);
  const [expandedName, setExpandedName] = useState<string | null>(null);

  const { data: tasks = [] } = useQuery({
    queryKey: ["tasks"],
    queryFn: () => tasksApi.list().then((r) => r.data),
  });

  const invalidate = () => queryClient.invalidateQueries({ queryKey: ["tasks"] });

  const handleTrigger = async (name: string) => {
    setTriggerStates((s) => ({ ...s, [name]: "pending" }));
    try {
      await tasksApi.trigger(name);
      setTriggerStates((s) => ({ ...s, [name]: "ok" }));
      setTimeout(() => setTriggerStates((s) => ({ ...s, [name]: "idle" })), 3000);
    } catch {
      setTriggerStates((s) => ({ ...s, [name]: "error" }));
      setTimeout(() => setTriggerStates((s) => ({ ...s, [name]: "idle" })), 3000);
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

  const inputBase =
    "w-full text-sm bg-[var(--bg-elevated)] border border-[var(--border)] rounded-[var(--radius-md)] px-2.5 py-1.5 text-[var(--text-strong)] focus:outline-none focus:border-[var(--accent)] transition-colors";

  return (
    <Card title={t("tasks.title")} subtitle={t("tasks.subtitle", { count: (tasks as TaskConfig[]).length })}>
      <div className="space-y-3">
        {(tasks as TaskConfig[]).length === 0 && !isCreating && (
          <p className="text-sm text-[var(--muted)] py-2">{t("tasks.empty")}</p>
        )}

        {(tasks as TaskConfig[]).map((task) => {
          const state = triggerStates[task.name] || "idle";
          const isExpanded = expandedName === task.name;
          const isEditing = editingTask?.name === task.name;

          return (
            <div key={task.name} className="border border-[var(--border)] rounded-[var(--radius-md)] overflow-hidden">
              <div
                className="flex items-center gap-3 px-3 py-2.5 bg-[var(--bg-elevated)] cursor-pointer hover:bg-[var(--bg-hover)] transition-colors"
                onClick={() => setExpandedName(isExpanded ? null : task.name)}
              >
                <span className={cn("w-1.5 h-1.5 rounded-full flex-shrink-0", task.enabled ? "bg-[var(--ok)]" : "bg-[var(--muted)]")} />
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-medium text-[var(--text-strong)]">{task.display_name || task.name}</span>
                    <span className="text-xs text-[var(--muted)]">{task.name}</span>
                    {!task.enabled && (
                      <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-[var(--secondary)] text-[var(--muted)]">{t("tasks.disabled")}</span>
                    )}
                  </div>
                  {task.description && <p className="text-xs text-[var(--muted)] truncate mt-0.5">{task.description}</p>}
                </div>
                <div className="flex items-center gap-1 flex-shrink-0" onClick={(e) => e.stopPropagation()}>
                  <button
                    onClick={() => handleTrigger(task.name)}
                    disabled={state === "pending" || !task.enabled}
                    className={cn(
                      "flex items-center gap-1 px-2.5 py-1 text-xs font-medium rounded-[var(--radius-md)] transition-all",
                      state === "ok"
                        ? "bg-[var(--ok-subtle)] text-[var(--ok)]"
                        : state === "error"
                          ? "bg-[var(--danger-subtle)] text-[var(--danger)]"
                          : "bg-[var(--accent)] text-[var(--primary-foreground)] hover:bg-[var(--accent-hover)]",
                      (state === "pending" || !task.enabled) && "opacity-50 cursor-not-allowed",
                    )}
                  >
                    <Play size={11} /> {triggerLabel(state)}
                  </button>
                  <button
                    onClick={() => { setEditingTask({ ...task }); setExpandedName(task.name); }}
                    className="p-1.5 rounded hover:bg-[var(--bg-hover)] text-[var(--muted)] hover:text-[var(--accent)] transition-colors"
                    title={tc("edit")}
                  >
                    <Pencil size={13} />
                  </button>
                  <button
                    onClick={() => {
                      if (confirm(t("tasks.confirmDelete", { name: task.display_name || task.name }))) {
                        deleteMut.mutate(task.name);
                      }
                    }}
                    className="p-1.5 rounded hover:bg-[var(--bg-hover)] text-[var(--muted)] hover:text-[var(--danger)] transition-colors"
                    title={tc("delete")}
                  >
                    <Trash2 size={13} />
                  </button>
                </div>
                {isExpanded ? <ChevronUp size={14} className="text-[var(--muted)]" /> : <ChevronDown size={14} className="text-[var(--muted)]" />}
              </div>

              {isExpanded && (
                <div className="px-3 py-3 border-t border-[var(--border)] bg-[var(--bg-base)]">
                  {isEditing ? (
                    <TaskEditForm
                      task={editingTask!}
                      onChange={setEditingTask}
                      onSave={() => {
                        const { name, ...rest } = editingTask!;
                        updateMut.mutate({ name, data: rest });
                      }}
                      onCancel={() => setEditingTask(null)}
                      isPending={updateMut.isPending}
                      inputBase={inputBase}
                    />
                  ) : (
                    <TaskDetail task={task} />
                  )}
                </div>
              )}
            </div>
          );
        })}

        {isCreating && (
          <div className="border border-[var(--accent)] rounded-[var(--radius-md)] overflow-hidden">
            <div className="flex items-center justify-between px-3 py-2 bg-[var(--bg-elevated)] border-b border-[var(--border)]">
              <span className="text-sm font-medium text-[var(--accent)]">{t("tasks.newTask")}</span>
              <button onClick={() => setIsCreating(false)} className="text-[var(--muted)] hover:text-[var(--text-strong)]">
                <X size={14} />
              </button>
            </div>
            <div className="px-3 py-3 bg-[var(--bg-base)]">
              <TaskCreateForm
                onSave={(task) => createMut.mutate(task)}
                onCancel={() => setIsCreating(false)}
                isPending={createMut.isPending}
                inputBase={inputBase}
              />
            </div>
          </div>
        )}

        {!isCreating && (
          <button
            onClick={() => setIsCreating(true)}
            className="flex items-center gap-1.5 px-3 py-1.5 text-sm font-medium rounded-[var(--radius-md)] border border-dashed border-[var(--border)] text-[var(--muted)] hover:border-[var(--accent)] hover:text-[var(--accent)] transition-colors"
          >
            <Plus size={14} /> {t("tasks.addTask")}
          </button>
        )}
      </div>
    </Card>
  );
}

function TaskDetail({ task }: { task: TaskConfig }) {
  const { t } = useTranslation("appconfig");
  const { t: tc } = useTranslation("common");
  return (
    <div className="space-y-2 text-xs text-[var(--muted)]">
      <div className="grid grid-cols-2 gap-x-4 gap-y-1">
        <span><span className="font-medium">{t("tasks.detailScope")}</span>{task.scope}</span>
        <span><span className="font-medium">{t("tasks.detailMemoryType")}</span>{task.memory_type}</span>
        <span><span className="font-medium">{t("tasks.detailImportance")}</span>{task.importance}</span>
        <span><span className="font-medium">{t("tasks.detailSource")}</span>{task.source || task.name}</span>
        <span><span className="font-medium">{t("tasks.detailAllowOutputTools")}</span>{task.allow_output_tools ? tc("on") : tc("off")}</span>
        <span><span className="font-medium">{t("tasks.detailSaveResultToMemory")}</span>{task.save_result_to_memory === false ? tc("off") : tc("on")}</span>
        <span><span className="font-medium">{t("tasks.detailReasoningEffort")}</span>{task.reasoning_effort || t("tasks.defaultReasoningEffort")}</span>
        {(task.tags ?? []).length > 0 && (
          <span className="col-span-2"><span className="font-medium">{t("tasks.detailTags")}</span>{(task.tags ?? []).join(", ")}</span>
        )}
        {(task.null_keywords ?? []).length > 0 && (
          <span className="col-span-2"><span className="font-medium">{t("tasks.detailNullKeywords")}</span>{(task.null_keywords ?? []).join(", ")}</span>
        )}
        {(task.tool_tags ?? []).length > 0 && (
          <span className="col-span-2"><span className="font-medium">{t("tasks.detailToolTags")}</span>{(task.tool_tags ?? []).join(", ")}</span>
        )}
        {task.model_id && (
          <span className="col-span-2"><span className="font-medium">{t("tasks.detailModelId")}</span>{task.model_id}</span>
        )}
      </div>
      <div>
        <p className="font-medium mb-1">{t("tasks.detailPrompt")}</p>
        <pre className="whitespace-pre-wrap text-[11px] bg-[var(--bg-elevated)] p-2 rounded border border-[var(--border)] max-h-48 overflow-y-auto leading-relaxed">
          {task.prompt}
        </pre>
      </div>
    </div>
  );
}

interface TaskEditFormProps {
  task: TaskConfig;
  onChange: (t: TaskConfig) => void;
  onSave: () => void;
  onCancel: () => void;
  isPending: boolean;
  inputBase: string;
}

function TaskEditForm({ task, onChange, onSave, onCancel, isPending, inputBase }: TaskEditFormProps) {
  const { t } = useTranslation("appconfig");
  const set = (key: keyof TaskConfig, value: unknown) => onChange({ ...task, [key]: value });

  interface PriorityItem { id: string; model: string }
  const { data: priorities = {} } = useQuery<Record<string, PriorityItem[]>>({
    queryKey: ["priorities"],
    queryFn: () => modelsApi.priorities().then(r => r.data),
  });
  const chatModels = priorities.chat ?? [];

  const scopeOptions = [
    { value: "global", label: t("tasks.scopeGlobal") },
    { value: "entity", label: t("tasks.scopeEntity") },
    { value: "any", label: t("tasks.scopeAny") },
  ];
  const memoryTypeOptions = [
    { value: "semantic", label: t("tasks.memoryTypeSemantic") },
    { value: "reflection", label: t("tasks.memoryTypeReflection") },
    { value: "entity", label: t("tasks.memoryTypeEntity") },
  ];
  const reasoningEffortOptions = REASONING_EFFORTS.map((item) => ({
    value: item.value,
    label: t(item.key),
  }));

  return (
    <div className="space-y-3">
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        <div className="flex flex-col gap-1">
          <label className="text-xs text-[var(--muted)] font-medium">{t("tasks.displayName")}</label>
          <input className={inputBase} value={task.display_name} onChange={(e) => set("display_name", e.target.value)} />
        </div>
        <div className="flex flex-col gap-1">
          <label className="text-xs text-[var(--muted)] font-medium">{t("tasks.description")}</label>
          <input className={inputBase} value={task.description} onChange={(e) => set("description", e.target.value)} />
        </div>
        <div className="flex flex-col gap-1">
          <label className="text-xs text-[var(--muted)] font-medium">{t("tasks.scope")}</label>
          <select className={inputBase} value={task.scope} onChange={(e) => set("scope", e.target.value)}>
            {scopeOptions.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
          </select>
        </div>
        <div className="flex flex-col gap-1">
          <label className="text-xs text-[var(--muted)] font-medium">{t("tasks.memoryType")}</label>
          <select className={inputBase} value={task.memory_type} onChange={(e) => set("memory_type", e.target.value)}>
            {memoryTypeOptions.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
          </select>
        </div>
        <div className="flex flex-col gap-1">
          <label className="text-xs text-[var(--muted)] font-medium">{t("tasks.importance")}</label>
          <input type="number" step="0.1" min="0" max="1" className={inputBase} value={task.importance}
            onChange={(e) => set("importance", parseFloat(e.target.value) || 0.5)} />
        </div>
        <div className="flex flex-col gap-1">
          <label className="text-xs text-[var(--muted)] font-medium">{t("tasks.sourceLabel")}</label>
          <input className={inputBase} value={task.source} onChange={(e) => set("source", e.target.value)} placeholder={task.name} />
        </div>
        <div className="flex flex-col gap-1">
          <label className="text-xs text-[var(--muted)] font-medium">{t("tasks.tagsLabel")}</label>
          <input className={inputBase} value={(task.tags ?? []).join(", ")}
            onChange={(e) => set("tags", e.target.value.split(",").map((s) => s.trim()).filter(Boolean))} />
        </div>
        <div className="flex flex-col gap-1">
          <label className="text-xs text-[var(--muted)] font-medium">{t("tasks.nullKeywords")}</label>
          <input className={inputBase} value={(task.null_keywords ?? []).join(", ")}
            onChange={(e) => set("null_keywords", e.target.value.split(",").map((s) => s.trim()).filter(Boolean))}
            placeholder={t("tasks.nullKeywordsPlaceholder")} />
        </div>
        <div className="flex flex-col gap-1">
          <label className="text-xs text-[var(--muted)] font-medium">{t("tasks.toolTags")}</label>
          <input className={inputBase} value={(task.tool_tags ?? []).join(", ")}
            onChange={(e) => set("tool_tags", e.target.value.split(",").map((s) => s.trim()).filter(Boolean))} />
        </div>
        <div className="flex flex-col gap-1">
          <label className="text-xs text-[var(--muted)] font-medium">{t("tasks.modelId")}</label>
          <select className={inputBase} value={task.model_id ?? ""}
            onChange={(e) => set("model_id", e.target.value || null)}>
            <option value="">{t("tasks.defaultModel")}</option>
            {chatModels.map((m) => <option key={m.id} value={m.id}>{m.id}</option>)}
          </select>
        </div>
        <div className="flex flex-col gap-1">
          <label className="text-xs text-[var(--muted)] font-medium">{t("tasks.reasoningEffort")}</label>
          <select
            className={inputBase}
            value={task.reasoning_effort ?? ""}
            onChange={(e) => set("reasoning_effort", (e.target.value || null) as ReasoningEffort | null)}
          >
            <option value="">{t("tasks.defaultReasoningEffort")}</option>
            {reasoningEffortOptions.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
          </select>
        </div>
        <div className="flex items-center justify-between md:col-span-2">
          <label className="text-xs text-[var(--muted)] font-medium">{t("tasks.allowOutputTools")}</label>
          <button
            onClick={() => set("allow_output_tools", !task.allow_output_tools)}
            className={cn(
              "relative inline-flex h-5 w-9 items-center rounded-full transition-colors",
              task.allow_output_tools ? "bg-[var(--accent)]" : "bg-[var(--border)]",
            )}
          >
            <span className={cn(
              "inline-block h-3.5 w-3.5 rounded-full bg-white shadow transition-transform",
              task.allow_output_tools ? "translate-x-4" : "translate-x-1",
            )} />
          </button>
        </div>
        <div className="flex items-center justify-between md:col-span-2">
          <label className="text-xs text-[var(--muted)] font-medium">{t("tasks.saveResultToMemory")}</label>
          <button
            onClick={() => set("save_result_to_memory", !(task.save_result_to_memory === false))}
            className={cn(
              "relative inline-flex h-5 w-9 items-center rounded-full transition-colors",
              task.save_result_to_memory === false ? "bg-[var(--border)]" : "bg-[var(--accent)]",
            )}
          >
            <span className={cn(
              "inline-block h-3.5 w-3.5 rounded-full bg-white shadow transition-transform",
              task.save_result_to_memory === false ? "translate-x-1" : "translate-x-4",
            )} />
          </button>
        </div>
        <div className="flex items-center justify-between md:col-span-2">
          <label className="text-xs text-[var(--muted)] font-medium">{t("tasks.enableTask")}</label>
          <button
            onClick={() => set("enabled", !task.enabled)}
            className={cn("relative inline-flex h-5 w-9 items-center rounded-full transition-colors", task.enabled ? "bg-[var(--accent)]" : "bg-[var(--border)]")}
          >
            <span className={cn("inline-block h-3.5 w-3.5 rounded-full bg-white shadow transition-transform", task.enabled ? "translate-x-4" : "translate-x-1")} />
          </button>
        </div>
      </div>
      <div className="flex flex-col gap-1">
        <label className="text-xs text-[var(--muted)] font-medium">{t("tasks.prompt")}</label>
        <textarea className={cn(inputBase, "min-h-[160px] resize-y font-mono text-[11px] leading-relaxed")}
          value={task.prompt} onChange={(e) => set("prompt", e.target.value)} />
      </div>
      <div className="flex items-center gap-2">
        <button onClick={onSave} disabled={isPending}
          className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-semibold rounded-[var(--radius-md)] bg-[var(--accent)] text-white hover:opacity-90 transition-all disabled:opacity-50">
          <Save size={12} /> {isPending ? t("actions.saving") : t("actions.save")}
        </button>
        <button onClick={onCancel}
          className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-[var(--radius-md)] border border-[var(--border)] text-[var(--muted)] hover:bg-[var(--bg-hover)]">
          <X size={12} /> {t("actions.cancel")}
        </button>
      </div>
    </div>
  );
}

function TaskCreateForm({ onSave, onCancel, isPending, inputBase }: { onSave: (t: TaskConfig) => void; onCancel: () => void; isPending: boolean; inputBase: string }) {
  const { t } = useTranslation("appconfig");
  const [task, setTask] = useState<TaskConfig>({ ...EMPTY_TASK });
  const set = (key: keyof TaskConfig, value: unknown) => setTask((prev) => ({ ...prev, [key]: value }));

  interface PriorityItem { id: string; model: string }
  const { data: priorities = {} } = useQuery<Record<string, PriorityItem[]>>({
    queryKey: ["priorities"],
    queryFn: () => modelsApi.priorities().then(r => r.data),
  });
  const chatModels = priorities.chat ?? [];

  const scopeOptions = [
    { value: "global", label: t("tasks.scopeGlobal") },
    { value: "entity", label: t("tasks.scopeEntity") },
    { value: "any", label: t("tasks.scopeAny") },
  ];
  const memoryTypeOptions = [
    { value: "semantic", label: t("tasks.memoryTypeSemantic") },
    { value: "reflection", label: t("tasks.memoryTypeReflection") },
    { value: "entity", label: t("tasks.memoryTypeEntity") },
  ];
  const reasoningEffortOptions = REASONING_EFFORTS.map((item) => ({
    value: item.value,
    label: t(item.key),
  }));

  return (
    <div className="space-y-3">
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        <div className="flex flex-col gap-1">
          <label className="text-xs text-[var(--muted)] font-medium">
            {t("tasks.taskName")} <span className="text-[var(--error)]">*</span>
          </label>
          <input className={inputBase} value={task.name} onChange={(e) => set("name", e.target.value)} placeholder={t("tasks.taskNamePlaceholder")} />
        </div>
        <div className="flex flex-col gap-1">
          <label className="text-xs text-[var(--muted)] font-medium">{t("tasks.displayName")}</label>
          <input className={inputBase} value={task.display_name} onChange={(e) => set("display_name", e.target.value)} placeholder={t("tasks.displayNamePlaceholder")} />
        </div>
        <div className="flex flex-col gap-1">
          <label className="text-xs text-[var(--muted)] font-medium">{t("tasks.description")}</label>
          <input className={inputBase} value={task.description} onChange={(e) => set("description", e.target.value)} />
        </div>
        <div className="flex flex-col gap-1">
          <label className="text-xs text-[var(--muted)] font-medium">{t("tasks.scope")}</label>
          <select className={inputBase} value={task.scope} onChange={(e) => set("scope", e.target.value)}>
            {scopeOptions.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
          </select>
        </div>
        <div className="flex flex-col gap-1">
          <label className="text-xs text-[var(--muted)] font-medium">{t("tasks.memoryType")}</label>
          <select className={inputBase} value={task.memory_type} onChange={(e) => set("memory_type", e.target.value)}>
            {memoryTypeOptions.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
          </select>
        </div>
        <div className="flex flex-col gap-1">
          <label className="text-xs text-[var(--muted)] font-medium">{t("tasks.importance")}</label>
          <input type="number" step="0.1" min="0" max="1" className={inputBase} value={task.importance}
            onChange={(e) => set("importance", parseFloat(e.target.value) || 0.5)} />
        </div>
        <div className="flex flex-col gap-1">
          <label className="text-xs text-[var(--muted)] font-medium">{t("tasks.nullKeywords")}</label>
          <input className={inputBase} value={(task.null_keywords ?? []).join(", ")}
            onChange={(e) => set("null_keywords", e.target.value.split(",").map((s) => s.trim()).filter(Boolean))}
            placeholder={t("tasks.nullKeywordsCreatePlaceholder")} />
        </div>
        <div className="flex flex-col gap-1">
          <label className="text-xs text-[var(--muted)] font-medium">{t("tasks.tagsLabel")}</label>
          <input className={inputBase} value={(task.tags ?? []).join(", ")}
            onChange={(e) => set("tags", e.target.value.split(",").map((s) => s.trim()).filter(Boolean))} />
        </div>
        <div className="flex flex-col gap-1 md:col-span-2">
          <label className="text-xs text-[var(--muted)] font-medium">{t("tasks.toolTags")}</label>
          <input className={inputBase} value={(task.tool_tags ?? []).join(", ")}
            onChange={(e) => set("tool_tags", e.target.value.split(",").map((s) => s.trim()).filter(Boolean))} />
        </div>
        <div className="flex flex-col gap-1">
          <label className="text-xs text-[var(--muted)] font-medium">{t("tasks.modelId")}</label>
          <select className={inputBase} value={task.model_id ?? ""}
            onChange={(e) => set("model_id", e.target.value || null)}>
            <option value="">{t("tasks.defaultModel")}</option>
            {chatModels.map((m) => <option key={m.id} value={m.id}>{m.id}</option>)}
          </select>
        </div>
        <div className="flex flex-col gap-1">
          <label className="text-xs text-[var(--muted)] font-medium">{t("tasks.reasoningEffort")}</label>
          <select
            className={inputBase}
            value={task.reasoning_effort ?? ""}
            onChange={(e) => set("reasoning_effort", (e.target.value || null) as ReasoningEffort | null)}
          >
            <option value="">{t("tasks.defaultReasoningEffort")}</option>
            {reasoningEffortOptions.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
          </select>
        </div>
        <div className="flex items-center justify-between md:col-span-2">
          <label className="text-xs text-[var(--muted)] font-medium">{t("tasks.allowOutputTools")}</label>
          <button
            onClick={() => set("allow_output_tools", !task.allow_output_tools)}
            className={cn(
              "relative inline-flex h-5 w-9 items-center rounded-full transition-colors",
              task.allow_output_tools ? "bg-[var(--accent)]" : "bg-[var(--border)]",
            )}
          >
            <span className={cn(
              "inline-block h-3.5 w-3.5 rounded-full bg-white shadow transition-transform",
              task.allow_output_tools ? "translate-x-4" : "translate-x-1",
            )} />
          </button>
        </div>
        <div className="flex items-center justify-between md:col-span-2">
          <label className="text-xs text-[var(--muted)] font-medium">{t("tasks.saveResultToMemory")}</label>
          <button
            onClick={() => set("save_result_to_memory", !(task.save_result_to_memory === false))}
            className={cn(
              "relative inline-flex h-5 w-9 items-center rounded-full transition-colors",
              task.save_result_to_memory === false ? "bg-[var(--border)]" : "bg-[var(--accent)]",
            )}
          >
            <span className={cn(
              "inline-block h-3.5 w-3.5 rounded-full bg-white shadow transition-transform",
              task.save_result_to_memory === false ? "translate-x-1" : "translate-x-4",
            )} />
          </button>
        </div>
      </div>
      <div className="flex flex-col gap-1">
        <label className="text-xs text-[var(--muted)] font-medium">
          {t("tasks.prompt")} <span className="text-[var(--error)]">*</span>
        </label>
        <textarea className={cn(inputBase, "min-h-[160px] resize-y font-mono text-[11px] leading-relaxed")}
          value={task.prompt} onChange={(e) => set("prompt", e.target.value)} placeholder={t("tasks.promptPlaceholder")} />
      </div>
      <div className="flex items-center gap-2">
        <button onClick={() => onSave(task)} disabled={isPending || !task.name.trim() || !task.prompt.trim()}
          className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-semibold rounded-[var(--radius-md)] bg-[var(--accent)] text-white hover:opacity-90 transition-all disabled:opacity-50">
          <Plus size={12} /> {isPending ? t("tasks.creating") : t("tasks.createTask")}
        </button>
        <button onClick={onCancel}
          className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-[var(--radius-md)] border border-[var(--border)] text-[var(--muted)] hover:bg-[var(--bg-hover)]">
          <X size={12} /> {t("actions.cancel")}
        </button>
      </div>
    </div>
  );
}
