import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import type { ReasoningEffort, TaskConfig } from "@/lib/types";
import { cn } from "@/lib/utils";
import { Input, Select, Switch, Textarea } from "@/components/ui";
import { ModelSelect } from "@/components/models/ModelSelect";
import { ReasoningEffortOptions } from "@/components/common/ReasoningEffortSelect";

export const EMPTY_TASK: TaskConfig = {
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

function Field({ label, required, children, className }: {
  label: string;
  required?: boolean;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <fieldset className={cn("flex min-w-0 flex-col gap-1", className)}>
      <legend className="mb-1 text-xs text-muted font-medium">
        {label} {required && <span className="text-danger">*</span>}
      </legend>
      {children}
    </fieldset>
  );
}

/** 任务表单字段（创建/编辑共用） */
export function TaskFormFields({ task, set, isCreate }: {
  task: TaskConfig;
  set: <K extends keyof TaskConfig>(key: K, value: TaskConfig[K]) => void;
  isCreate?: boolean;
}) {
  const { t } = useTranslation("appconfig");

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

  return (
    <>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        {isCreate && (
          <Field label={t("tasks.taskName")} required>
            <Input value={task.name} onChange={(e) => set("name", e.target.value)}
              placeholder={t("tasks.taskNamePlaceholder")} />
          </Field>
        )}
        <Field label={t("workbench:tasks.fieldFolder")}><Input aria-label={t("workbench:tasks.fieldFolder")} value={task.folder ?? ""} onChange={(e) => set("folder", e.target.value)} placeholder="dev/backend" /></Field>
        <Field label={t("tasks.expiresAt")}><Input aria-label={t("tasks.expiresAt")} type="datetime-local" value={task.expires_at?.replace(" ", "T") ?? ""} onChange={(e) => set("expires_at", e.target.value.replace("T", " "))} /></Field>
        <Field label={t("tasks.displayName")}>
          <Input value={task.display_name} onChange={(e) => set("display_name", e.target.value)}
            placeholder={isCreate ? t("tasks.displayNamePlaceholder") : undefined} />
        </Field>
        <Field label={t("tasks.description")}>
          <Input value={task.description} onChange={(e) => set("description", e.target.value)} />
        </Field>
        <Field label={t("tasks.scope")}>
          <Select className="w-full" value={task.scope} onChange={(e) => set("scope", e.target.value)}>
            {scopeOptions.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
          </Select>
        </Field>
        <Field label={t("tasks.memoryType")}>
          <Select className="w-full" value={task.memory_type} onChange={(e) => set("memory_type", e.target.value)}>
            {memoryTypeOptions.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
          </Select>
        </Field>
        <Field label={t("tasks.importance")}>
          <Input aria-label={t("tasks.importance")} type="number" step="0.1" min="0" max="1" value={task.importance}
            onChange={(e) => set("importance", e.target.value === "" ? 0 : Math.min(1, Math.max(0, Number(e.target.value))))} />
        </Field>
        {(
          <Field label={t("tasks.sourceLabel")}>
            <Input value={task.source} onChange={(e) => set("source", e.target.value)} placeholder={task.name} />
          </Field>
        )}
        <Field label={t("tasks.tagsLabel")}>
          <TokenInput value={task.tags ?? []} onChange={(value) => set("tags", value)} />
        </Field>
        <Field label={t("tasks.nullKeywords")}>
          <TokenInput value={task.null_keywords ?? []} onChange={(value) => set("null_keywords", value)}
            placeholder={isCreate ? t("tasks.nullKeywordsCreatePlaceholder") : t("tasks.nullKeywordsPlaceholder")} />
        </Field>
        <Field label={t("tasks.toolTags")} className={isCreate ? "md:col-span-2" : undefined}>
          <TokenInput value={task.tool_tags ?? []} onChange={(value) => set("tool_tags", value)} />
        </Field>
        <Field label={t("tasks.modelId")}>
          <ModelSelect
            modelType="chat"
            allowEmpty
            value={task.model_id ?? ""}
            onChange={(id) => set("model_id", id || null)}
          />
        </Field>
        <Field label={t("tasks.reasoningEffort")}>
          <Select className="w-full" value={task.reasoning_effort ?? ""}
            onChange={(e) => set("reasoning_effort", (e.target.value || null) as ReasoningEffort | null)}>
            <option value="">{t("tasks.defaultReasoningEffort")}</option>
            <ReasoningEffortOptions t={t} keyPrefix="tasks." />
          </Select>
        </Field>
        <Field label={t("tasks.triggerEvent")}>
          <Select className="w-full" value={task.trigger_event ?? ""}
            onChange={(e) => set("trigger_event", e.target.value || undefined)}>
            <option value="">{t("tasks.triggerEventNone")}</option>
            <option value="after_reply">{t("tasks.triggerEventAfterReply")}</option>
            <option value="context_pressure">{t("tasks.triggerEventContextPressure")}</option>
            <option value="delegation_resolved">{t("tasks.triggerEventDelegationResolved")}</option>
          </Select>
        </Field>
        <div className="flex items-center justify-between md:col-span-2">
          <label className="text-xs text-muted font-medium">{t("tasks.allowOutputTools")}</label>
          <Switch label={t("tasks.allowOutputTools")} checked={task.allow_output_tools ?? false} onChange={(v) => set("allow_output_tools", v)} />
        </div>
        <div className="flex items-center justify-between md:col-span-2">
          <label className="text-xs text-muted font-medium">{t("tasks.saveResultToMemory")}</label>
          <Switch label={t("tasks.saveResultToMemory")} checked={task.save_result_to_memory !== false}
            onChange={(v) => set("save_result_to_memory", v)} />
        </div>
        {(
          <div className="flex items-center justify-between md:col-span-2">
            <label className="text-xs text-muted font-medium">{t("tasks.enableTask")}</label>
            <Switch label={t("tasks.enableTask")} checked={task.enabled} onChange={(v) => set("enabled", v)} />
          </div>
        )}
      </div>
      <Field label={t("tasks.prompt")} required={isCreate}>
        <Textarea className="min-h-[160px] font-mono text-[11px] leading-relaxed"
          value={task.prompt} onChange={(e) => set("prompt", e.target.value)}
          placeholder={isCreate ? t("tasks.promptPlaceholder") : undefined} />
      </Field>
    </>
  );
}


export function TaskDetail({ task }: { task: TaskConfig }) {
  const { t } = useTranslation("appconfig");
  const { t: tc } = useTranslation("common");
  return (
    <div className="space-y-2 text-xs text-muted">
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-x-4 gap-y-1">
        <span><span className="font-medium">{t("tasks.detailScope")}</span>{task.scope}</span>
        <span><span className="font-medium">{t("tasks.detailMemoryType")}</span>{task.memory_type}</span>
        <span><span className="font-medium">{t("tasks.detailImportance")}</span>{task.importance}</span>
        <span><span className="font-medium">{t("tasks.detailSource")}</span>{task.source || task.name}</span>
        <span><span className="font-medium">{t("tasks.detailAllowOutputTools")}</span>{task.allow_output_tools ? tc("on") : tc("off")}</span>
        <span><span className="font-medium">{t("tasks.detailSaveResultToMemory")}</span>{task.save_result_to_memory === false ? tc("off") : tc("on")}</span>
        <span><span className="font-medium">{t("tasks.detailReasoningEffort")}</span>{task.reasoning_effort || t("tasks.defaultReasoningEffort")}</span>
        {(task.tags ?? []).length > 0 && (
          <span className="sm:col-span-2"><span className="font-medium">{t("tasks.detailTags")}</span>{(task.tags ?? []).join(", ")}</span>
        )}
        {(task.null_keywords ?? []).length > 0 && (
          <span className="sm:col-span-2"><span className="font-medium">{t("tasks.detailNullKeywords")}</span>{(task.null_keywords ?? []).join(", ")}</span>
        )}
        {(task.tool_tags ?? []).length > 0 && (
          <span className="sm:col-span-2"><span className="font-medium">{t("tasks.detailToolTags")}</span>{(task.tool_tags ?? []).join(", ")}</span>
        )}
        {task.model_id && (
          <span className="sm:col-span-2"><span className="font-medium">{t("tasks.detailModelId")}</span>{task.model_id}</span>
        )}
      </div>
      <div>
        <p className="font-medium mb-1">{t("tasks.detailPrompt")}</p>
        <pre className="whitespace-pre-wrap text-[11px] bg-elevated p-2 rounded border border-border max-h-48 overflow-y-auto leading-relaxed">
          {task.prompt}
        </pre>
      </div>
    </div>
  );
}

function TokenInput({ value, onChange, placeholder }: { value: string[]; onChange: (value: string[]) => void; placeholder?: string }) {
  const [text, setText] = useState(value.join(", "));
  useEffect(() => {
    if (JSON.stringify(text.split(",").map((item) => item.trim()).filter(Boolean)) !== JSON.stringify(value)) setText(value.join(", "));
  }, [value, text]);
  return <Input value={text} placeholder={placeholder} onChange={(event) => {
    setText(event.target.value);
    onChange(event.target.value.split(",").map((item) => item.trim()).filter(Boolean));
  }} />;
}
