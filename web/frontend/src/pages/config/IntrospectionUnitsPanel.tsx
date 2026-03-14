import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import {
  introspectionUnitsApi,
  type IntrospectionUnitConfig,
} from "@/lib/api";
import { Card } from "@/components/common/Card";
import { cn } from "@/lib/utils";
import {
  Plus,
  Pencil,
  Trash2,
  X,
  ChevronDown,
  ChevronUp,
  Save,
} from "lucide-react";

const EMPTY_UNIT: IntrospectionUnitConfig = {
  name: "",
  display_name: "",
  description: "",
  scope: "global",
  enabled: true,
  memory_type: "reflection",
  importance: 0.5,
  tags: [],
  source: "",
  null_keywords: [],
  prompt: "",
};

export function IntrospectionUnitsPanel() {
  const { t } = useTranslation("appconfig");
  const { t: tc } = useTranslation("common");
  const queryClient = useQueryClient();
  const [editingUnit, setEditingUnit] = useState<IntrospectionUnitConfig | null>(null);
  const [isCreating, setIsCreating] = useState(false);
  const [expandedName, setExpandedName] = useState<string | null>(null);

  const { data: units = [], isLoading } = useQuery({
    queryKey: ["introspectionUnits"],
    queryFn: () => introspectionUnitsApi.list().then((r) => r.data),
  });

  const invalidate = () => queryClient.invalidateQueries({ queryKey: ["introspectionUnits"] });

  const createMut = useMutation({
    mutationFn: (u: IntrospectionUnitConfig) => introspectionUnitsApi.create(u),
    onSuccess: () => {
      invalidate();
      setIsCreating(false);
    },
  });

  const updateMut = useMutation({
    mutationFn: ({ name, data }: { name: string; data: Partial<IntrospectionUnitConfig> }) => introspectionUnitsApi.update(name, data),
    onSuccess: () => {
      invalidate();
      setEditingUnit(null);
    },
  });

  const deleteMut = useMutation({
    mutationFn: (name: string) => introspectionUnitsApi.delete(name),
    onSuccess: () => invalidate(),
  });

  const inputBase =
    "w-full text-sm bg-[var(--bg-elevated)] border border-[var(--border)] rounded-[var(--radius-md)] px-2.5 py-1.5 text-[var(--text-strong)] focus:outline-none focus:border-[var(--accent)] transition-colors";

  if (isLoading) {
    return (
      <Card title={t("units.title")}>
        <p className="text-sm text-[var(--muted)]">{tc("loading")}</p>
      </Card>
    );
  }

  return (
    <Card title={t("units.title")} subtitle={t("units.subtitle")}>
      <div className="space-y-3">
        {units.length === 0 && !isCreating && <p className="text-sm text-[var(--muted)] py-2">{t("units.empty")}</p>}

        {units.map((unit) => {
          const isExpanded = expandedName === unit.name;
          const isEditing = editingUnit?.name === unit.name;

          return (
            <div key={unit.name} className="border border-[var(--border)] rounded-[var(--radius-md)] overflow-hidden">
              <div
                className="flex items-center gap-3 px-3 py-2.5 bg-[var(--bg-elevated)] cursor-pointer hover:bg-[var(--bg-hover)] transition-colors"
                onClick={() => setExpandedName(isExpanded ? null : unit.name)}
              >
                <span className={cn("w-1.5 h-1.5 rounded-full flex-shrink-0", unit.enabled ? "bg-[var(--ok)]" : "bg-[var(--muted)]")} />
                <div className="flex-1 min-w-0">
                  <span className="text-sm font-medium text-[var(--text-strong)]">{unit.display_name || unit.name}</span>
                  <span className="ml-2 text-xs text-[var(--muted)]">{unit.name}</span>
                  {unit.description && <p className="text-xs text-[var(--muted)] truncate mt-0.5">{unit.description}</p>}
                </div>
                <span className="text-xs px-1.5 py-0.5 rounded bg-[var(--secondary)] text-[var(--muted)] flex-shrink-0">{unit.scope}</span>
                <div className="flex items-center gap-1 flex-shrink-0" onClick={(e) => e.stopPropagation()}>
                  <button
                    onClick={() => setEditingUnit({ ...unit })}
                    className="p-1.5 rounded hover:bg-[var(--bg-hover)] text-[var(--muted)] hover:text-[var(--accent)] transition-colors"
                    title={tc("edit")}
                  >
                    <Pencil size={13} />
                  </button>
                  <button
                    onClick={() => {
                      if (confirm(t("units.confirmDelete", { name: unit.display_name || unit.name }))) {
                        deleteMut.mutate(unit.name);
                      }
                    }}
                    className="p-1.5 rounded hover:bg-[var(--bg-hover)] text-[var(--muted)] hover:text-[var(--error)] transition-colors"
                    title={tc("delete")}
                  >
                    <Trash2 size={13} />
                  </button>
                </div>
                {isExpanded ? (
                  <ChevronUp size={14} className="text-[var(--muted)]" />
                ) : (
                  <ChevronDown size={14} className="text-[var(--muted)]" />
                )}
              </div>

              {isExpanded && (
                <div className="px-3 py-3 border-t border-[var(--border)] bg-[var(--bg-base)]">
                  {isEditing ? (
                    <UnitEditForm
                      unit={editingUnit!}
                      onChange={setEditingUnit}
                      onSave={() => {
                        const { name, ...rest } = editingUnit!;
                        updateMut.mutate({ name, data: rest });
                      }}
                      onCancel={() => setEditingUnit(null)}
                      isPending={updateMut.isPending}
                      inputBase={inputBase}
                    />
                  ) : (
                    <UnitDetail unit={unit} />
                  )}
                </div>
              )}
            </div>
          );
        })}

        {isCreating && (
          <div className="border border-[var(--accent)] rounded-[var(--radius-md)] overflow-hidden">
            <div className="flex items-center justify-between px-3 py-2 bg-[var(--bg-elevated)] border-b border-[var(--border)]">
              <span className="text-sm font-medium text-[var(--accent)]">{t("units.newUnit")}</span>
              <button onClick={() => setIsCreating(false)} className="text-[var(--muted)] hover:text-[var(--text-strong)]">
                <X size={14} />
              </button>
            </div>
            <div className="px-3 py-3 bg-[var(--bg-base)]">
              <UnitCreateForm onSave={(unit) => createMut.mutate(unit)} onCancel={() => setIsCreating(false)} isPending={createMut.isPending} inputBase={inputBase} />
            </div>
          </div>
        )}

        {!isCreating && (
          <button
            onClick={() => setIsCreating(true)}
            className="flex items-center gap-1.5 px-3 py-1.5 text-sm font-medium rounded-[var(--radius-md)] border border-dashed border-[var(--border)] text-[var(--muted)] hover:border-[var(--accent)] hover:text-[var(--accent)] transition-colors"
          >
            <Plus size={14} /> {t("units.addUnit")}
          </button>
        )}
      </div>
    </Card>
  );
}

function UnitDetail({ unit }: { unit: IntrospectionUnitConfig }) {
  const { t } = useTranslation("appconfig");

  return (
    <div className="space-y-2 text-xs text-[var(--muted)]">
      <div className="grid grid-cols-2 gap-x-4 gap-y-1">
        <span>
          <span className="font-medium">{t("units.detailScope")}</span>
          {unit.scope}
        </span>
        <span>
          <span className="font-medium">{t("units.detailMemoryType")}</span>
          {unit.memory_type}
        </span>
        <span>
          <span className="font-medium">{t("units.detailImportance")}</span>
          {unit.importance}
        </span>
        <span>
          <span className="font-medium">{t("units.detailSource")}</span>
          {unit.source || unit.name}
        </span>
        {unit.tags.length > 0 && (
          <span className="col-span-2">
            <span className="font-medium">{t("units.detailTags")}</span>
            {unit.tags.join(", ")}
          </span>
        )}
        {unit.null_keywords.length > 0 && (
          <span className="col-span-2">
            <span className="font-medium">{t("units.detailNullKeywords")}</span>
            {unit.null_keywords.join(", ")}
          </span>
        )}
      </div>
      <div>
        <p className="font-medium mb-1">{t("units.detailPrompt")}</p>
        <pre className="whitespace-pre-wrap text-[11px] bg-[var(--bg-elevated)] p-2 rounded border border-[var(--border)] max-h-48 overflow-y-auto leading-relaxed">
          {unit.prompt}
        </pre>
      </div>
    </div>
  );
}

interface UnitFormProps {
  unit: IntrospectionUnitConfig;
  onChange: (u: IntrospectionUnitConfig) => void;
  onSave: () => void;
  onCancel: () => void;
  isPending: boolean;
  inputBase: string;
  isCreate?: boolean;
}

function UnitEditForm({ unit, onChange, onSave, onCancel, isPending, inputBase }: UnitFormProps) {
  const { t } = useTranslation("appconfig");
  const set = (key: keyof IntrospectionUnitConfig, value: unknown) => onChange({ ...unit, [key]: value });

  const scopeOptions = [
    { value: "global", label: t("units.scopeGlobal") },
    { value: "entity", label: t("units.scopeEntity") },
    { value: "any", label: t("units.scopeAny") },
  ];

  const memoryTypeOptions = [
    { value: "reflection", label: t("units.memoryReflection") },
    { value: "semantic", label: t("units.memorySemantic") },
  ];

  return (
    <div className="space-y-3">
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        <div className="flex flex-col gap-1">
          <label className="text-xs text-[var(--muted)] font-medium">{t("units.displayName")}</label>
          <input className={inputBase} value={unit.display_name} onChange={(e) => set("display_name", e.target.value)} />
        </div>
        <div className="flex flex-col gap-1">
          <label className="text-xs text-[var(--muted)] font-medium">{t("units.description")}</label>
          <input className={inputBase} value={unit.description} onChange={(e) => set("description", e.target.value)} />
        </div>
        <div className="flex flex-col gap-1">
          <label className="text-xs text-[var(--muted)] font-medium">{t("units.scope")}</label>
          <select className={inputBase} value={unit.scope} onChange={(e) => set("scope", e.target.value as IntrospectionUnitConfig["scope"])}>
            {scopeOptions.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
        </div>
        <div className="flex flex-col gap-1">
          <label className="text-xs text-[var(--muted)] font-medium">{t("units.memoryType")}</label>
          <select className={inputBase} value={unit.memory_type} onChange={(e) => set("memory_type", e.target.value as IntrospectionUnitConfig["memory_type"])}>
            {memoryTypeOptions.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
        </div>
        <div className="flex flex-col gap-1">
          <label className="text-xs text-[var(--muted)] font-medium">{t("units.importance")}</label>
          <input type="number" step="0.1" min="0" max="1" className={inputBase} value={unit.importance} onChange={(e) => set("importance", parseFloat(e.target.value) || 0.5)} />
        </div>
        <div className="flex flex-col gap-1">
          <label className="text-xs text-[var(--muted)] font-medium">{t("units.sourceLabel")}</label>
          <input className={inputBase} value={unit.source} onChange={(e) => set("source", e.target.value)} placeholder={unit.name} />
        </div>
        <div className="flex flex-col gap-1">
          <label className="text-xs text-[var(--muted)] font-medium">{t("units.tagsLabel")}</label>
          <input className={inputBase} value={unit.tags.join(", ")} onChange={(e) => set("tags", e.target.value.split(",").map((s) => s.trim()).filter(Boolean))} />
        </div>
        <div className="flex flex-col gap-1">
          <label className="text-xs text-[var(--muted)] font-medium">{t("units.nullKeywords")}</label>
          <input
            className={inputBase}
            value={unit.null_keywords.join(", ")}
            onChange={(e) => set("null_keywords", e.target.value.split(",").map((s) => s.trim()).filter(Boolean))}
            placeholder={t("units.nullKeywordsPlaceholder")}
          />
        </div>
        <div className="flex items-center justify-between md:col-span-2">
          <label className="text-xs text-[var(--muted)] font-medium">{t("units.enableUnit")}</label>
          <button
            onClick={() => set("enabled", !unit.enabled)}
            className={cn("relative inline-flex h-5 w-9 items-center rounded-full transition-colors", unit.enabled ? "bg-[var(--accent)]" : "bg-[var(--border)]")}
          >
            <span className={cn("inline-block h-3.5 w-3.5 rounded-full bg-white shadow transition-transform", unit.enabled ? "translate-x-4" : "translate-x-1")} />
          </button>
        </div>
      </div>
      <div className="flex flex-col gap-1">
        <label className="text-xs text-[var(--muted)] font-medium">{t("units.prompt")}</label>
        <textarea className={cn(inputBase, "min-h-[160px] resize-y font-mono text-[11px] leading-relaxed")} value={unit.prompt} onChange={(e) => set("prompt", e.target.value)} />
      </div>
      <div className="flex items-center gap-2">
        <button
          onClick={onSave}
          disabled={isPending}
          className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-semibold rounded-[var(--radius-md)] bg-[var(--accent)] text-white hover:opacity-90 transition-all disabled:opacity-50"
        >
          <Save size={12} /> {isPending ? t("actions.saving") : t("actions.save")}
        </button>
        <button onClick={onCancel} className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-[var(--radius-md)] border border-[var(--border)] text-[var(--muted)] hover:bg-[var(--bg-hover)]">
          <X size={12} /> {t("actions.cancel")}
        </button>
      </div>
    </div>
  );
}

function UnitCreateForm({ onSave, onCancel, isPending, inputBase }: { onSave: (u: IntrospectionUnitConfig) => void; onCancel: () => void; isPending: boolean; inputBase: string }) {
  const { t } = useTranslation("appconfig");
  const [unit, setUnit] = useState<IntrospectionUnitConfig>({ ...EMPTY_UNIT });
  const set = (key: keyof IntrospectionUnitConfig, value: unknown) => setUnit((prev) => ({ ...prev, [key]: value }));

  const scopeOptions = [
    { value: "global", label: t("units.scopeGlobal") },
    { value: "entity", label: t("units.scopeEntity") },
    { value: "any", label: t("units.scopeAny") },
  ];

  const memoryTypeOptions = [
    { value: "reflection", label: t("units.memoryReflection") },
    { value: "semantic", label: t("units.memorySemantic") },
  ];

  return (
    <div className="space-y-3">
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        <div className="flex flex-col gap-1">
          <label className="text-xs text-[var(--muted)] font-medium">
            {t("units.unitName")} <span className="text-[var(--error)]">*</span>
          </label>
          <input className={inputBase} value={unit.name} onChange={(e) => set("name", e.target.value)} placeholder={t("units.unitNamePlaceholder")} />
        </div>
        <div className="flex flex-col gap-1">
          <label className="text-xs text-[var(--muted)] font-medium">{t("units.displayName")}</label>
          <input className={inputBase} value={unit.display_name} onChange={(e) => set("display_name", e.target.value)} placeholder={t("units.displayNamePlaceholder")} />
        </div>
        <div className="flex flex-col gap-1">
          <label className="text-xs text-[var(--muted)] font-medium">{t("units.description")}</label>
          <input className={inputBase} value={unit.description} onChange={(e) => set("description", e.target.value)} />
        </div>
        <div className="flex flex-col gap-1">
          <label className="text-xs text-[var(--muted)] font-medium">{t("units.scope")}</label>
          <select className={inputBase} value={unit.scope} onChange={(e) => set("scope", e.target.value as IntrospectionUnitConfig["scope"])}>
            {scopeOptions.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
        </div>
        <div className="flex flex-col gap-1">
          <label className="text-xs text-[var(--muted)] font-medium">{t("units.memoryType")}</label>
          <select className={inputBase} value={unit.memory_type} onChange={(e) => set("memory_type", e.target.value as IntrospectionUnitConfig["memory_type"])}>
            {memoryTypeOptions.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
        </div>
        <div className="flex flex-col gap-1">
          <label className="text-xs text-[var(--muted)] font-medium">{t("units.importance")}</label>
          <input type="number" step="0.1" min="0" max="1" className={inputBase} value={unit.importance} onChange={(e) => set("importance", parseFloat(e.target.value) || 0.5)} />
        </div>
        <div className="flex flex-col gap-1">
          <label className="text-xs text-[var(--muted)] font-medium">{t("units.nullKeywords")}</label>
          <input
            className={inputBase}
            value={unit.null_keywords.join(", ")}
            onChange={(e) => set("null_keywords", e.target.value.split(",").map((s) => s.trim()).filter(Boolean))}
            placeholder={t("units.nullKeywordsCreatePlaceholder")}
          />
        </div>
        <div className="flex flex-col gap-1">
          <label className="text-xs text-[var(--muted)] font-medium">{t("units.tagsLabel")}</label>
          <input className={inputBase} value={unit.tags.join(", ")} onChange={(e) => set("tags", e.target.value.split(",").map((s) => s.trim()).filter(Boolean))} />
        </div>
      </div>
      <div className="flex flex-col gap-1">
        <label className="text-xs text-[var(--muted)] font-medium">
          {t("units.prompt")} <span className="text-[var(--error)]">*</span>
        </label>
        <textarea
          className={cn(inputBase, "min-h-[160px] resize-y font-mono text-[11px] leading-relaxed")}
          value={unit.prompt}
          onChange={(e) => set("prompt", e.target.value)}
          placeholder={t("units.promptPlaceholder")}
        />
      </div>
      <div className="flex items-center gap-2">
        <button
          onClick={() => onSave(unit)}
          disabled={isPending || !unit.name.trim() || !unit.prompt.trim()}
          className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-semibold rounded-[var(--radius-md)] bg-[var(--accent)] text-white hover:opacity-90 transition-all disabled:opacity-50"
        >
          <Plus size={12} /> {isPending ? t("units.creating") : t("units.createUnit")}
        </button>
        <button onClick={onCancel} className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-[var(--radius-md)] border border-[var(--border)] text-[var(--muted)] hover:bg-[var(--bg-hover)]">
          <X size={12} /> {t("actions.cancel")}
        </button>
      </div>
    </div>
  );
}
