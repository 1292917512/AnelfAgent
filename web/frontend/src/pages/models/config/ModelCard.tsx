import { useTranslation } from "react-i18next";
import { ChevronDown, ChevronRight, Save, Scan, Trash2 } from "lucide-react";
import type { ModelConfig, UpdateModelConfig } from "@/lib/types";
import { cn } from "@/lib/utils";
import { Button, Input, Select, Textarea } from "@/components/ui";
import { MODEL_TYPE_OPTIONS, ModelBadges, type JsonField } from "./shared";

const EDITABLE_FIELDS = ["model", "temperature", "top_p", "max_tokens", "context_window", "frequency_penalty", "presence_penalty", "timeout"] as const;

/** 单个模型卡片：头部徽标 + 展开编辑器 */
export function ModelCard({
  model,
  editing,
  expanded,
  onToggle,
  onStartEdit,
  onEditChange,
  onSave,
  onProbe,
  onAutoConfig,
  onRemove,
  jsonDrafts,
  onJsonDraftChange,
  jsonErrors,
  testResult,
  isPending,
}: {
  model: ModelConfig;
  /** 编辑草稿（非 null 表示编辑中），已与 model 合并 */
  editing: UpdateModelConfig | null;
  expanded: boolean;
  onToggle: () => void;
  onStartEdit: () => void;
  onEditChange: (patch: Partial<UpdateModelConfig>) => void;
  onSave: () => void;
  onProbe: () => void;
  onAutoConfig: () => void;
  onRemove: () => void;
  jsonDrafts: Record<JsonField, string>;
  onJsonDraftChange: (field: JsonField, value: string) => void;
  jsonErrors: Partial<Record<JsonField, string>>;
  testResult: string;
  isPending: boolean;
}) {
  const { t } = useTranslation(["models", "common"]);
  const me: ModelConfig = editing ? { ...model, ...editing } : model;

  return (
    <div className={cn(
      "rounded-md border transition-all",
      expanded ? "border-accent2 bg-elevated" : "border-border bg-elevated hover:border-border-strong",
    )}>
      <div className="flex items-center justify-between gap-2 p-3 cursor-pointer" onClick={onToggle}>
        <div className="flex items-center gap-2 min-w-0 flex-wrap">
          {expanded ? <ChevronDown size={14} className="text-accent2 shrink-0" /> : <ChevronRight size={14} className="text-muted shrink-0" />}
          <span className="text-sm font-medium text-heading truncate">{model.id}</span>
          <span className="text-xs text-muted truncate hidden sm:inline">{model.model}</span>
          <ModelBadges model={me} />
        </div>
        <button
          onClick={(e) => { e.stopPropagation(); onRemove(); }}
          className="p-1 rounded text-muted hover:text-danger transition-colors shrink-0"
        >
          <Trash2 size={13} />
        </button>
      </div>

      {expanded && (
        <div className="border-t border-border p-3 space-y-3">
          <div className="flex gap-2 flex-wrap">
            <Button variant="secondary" size="sm" onClick={onProbe}>
              <Scan size={12} /> {t("probeCapability")}
            </Button>
            <Button variant="secondary" size="sm" onClick={onAutoConfig} className="border-accent text-accent hover:bg-accent-subtle">
              <Scan size={12} /> {t("autoConfig")}
            </Button>
            {editing ? (
              <Button variant="primary" size="sm" onClick={onSave} loading={isPending}>
                <Save size={12} /> {t("common:save")}
              </Button>
            ) : (
              <Button variant="secondary" size="sm" onClick={onStartEdit}>{t("common:edit")}</Button>
            )}
          </div>

          <div className="grid grid-cols-2 md:grid-cols-3 gap-3">
            {EDITABLE_FIELDS.map((k) => (
              <div key={k} className="space-y-1">
                <label className="text-xs font-medium text-muted">{k}</label>
                <Input
                  type={k === "model" ? "text" : "number"}
                  step={k === "max_tokens" || k === "context_window" ? 1 : "any"}
                  value={me[k] ?? ""}
                  readOnly={!editing}
                  onChange={(e) => {
                    if (!editing) return;
                    const value = k === "model" ? e.target.value : Number(e.target.value);
                    onEditChange({ [k]: value });
                  }}
                />
              </div>
            ))}
            <div className="space-y-1">
              <label className="text-xs font-medium text-muted">{t("modelFields.chat_protocol")}</label>
              <Select
                className="w-full"
                value={me.chat_protocol ?? "chat_completions"}
                disabled={!editing}
                onChange={(e) => editing && onEditChange({ chat_protocol: e.target.value as ModelConfig["chat_protocol"] })}
              >
                <option value="chat_completions">{t("chatProtocol.chat_completions")}</option>
                <option value="responses">{t("chatProtocol.responses")}</option>
                <option value="auto">{t("chatProtocol.auto")}</option>
              </Select>
              <p className="text-[11px] text-muted opacity-70">{t("modelFields.chat_protocolHint")}</p>
            </div>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            {(["request_params", "extra_body"] as const).map((field) => (
              <div key={field} className="space-y-1">
                <label className="text-xs font-medium text-muted">{t(`modelFields.${field}`)}</label>
                <Textarea
                  rows={6}
                  value={editing ? jsonDrafts[field] : JSON.stringify(me[field], null, 2)}
                  readOnly={!editing}
                  onChange={(e) => onJsonDraftChange(field, e.target.value)}
                  className={cn("font-mono text-xs", jsonErrors[field] && "border-danger")}
                />
                <p className={cn("text-xs", jsonErrors[field] ? "text-danger" : "text-muted")}>
                  {jsonErrors[field] ?? t(`modelFields.${field}Hint`)}
                </p>
              </div>
            ))}
          </div>

          <div className="flex flex-wrap gap-3 items-center">
            <label className="flex items-center gap-2 cursor-pointer">
              <input type="checkbox" checked={me.supports_vision} disabled={!editing}
                onChange={(e) => editing && onEditChange({ supports_vision: e.target.checked })}
                className="accent-accent2 w-3.5 h-3.5" />
              <span className="text-xs text-foreground">{t("vision")}</span>
            </label>
            {me.supports_vision && (
              <Select
                value={me.vision_format}
                disabled={!editing}
                onChange={(e) => editing && onEditChange({ vision_format: e.target.value })}
                className="!h-7 text-xs"
              >
                <option value="base64">base64</option>
                <option value="url">url</option>
                <option value="both">both</option>
              </Select>
            )}
            <label className="flex items-center gap-2 cursor-pointer">
              <input type="checkbox" checked={me.supports_tools} disabled={!editing}
                onChange={(e) => editing && onEditChange({ supports_tools: e.target.checked })}
                className="accent-accent w-3.5 h-3.5" />
              <span className="text-xs text-foreground">{t("toolCall")}</span>
            </label>
            <label className="flex items-center gap-2 cursor-pointer">
              <input type="checkbox" checked={me.supports_reasoning} disabled={!editing}
                onChange={(e) => editing && onEditChange({ supports_reasoning: e.target.checked })}
                className="accent-[rgb(168,85,247)] w-3.5 h-3.5" />
              <span className="text-xs text-foreground">{t("deepThinking")}</span>
            </label>
            <label className="flex items-center gap-2 cursor-pointer" title={t("forcedToolChoiceHint")}>
              <input type="checkbox" checked={me.supports_forced_tool_choice} disabled={!editing}
                onChange={(e) => editing && onEditChange({ supports_forced_tool_choice: e.target.checked })}
                className="accent-accent w-3.5 h-3.5" />
              <span className="text-xs text-foreground">{t("forcedToolChoice")}</span>
            </label>
          </div>

          <div>
            <p className="text-xs font-medium text-muted mb-1">{t("modelTypes")}</p>
            <div className="flex flex-wrap gap-1.5">
              {MODEL_TYPE_OPTIONS.map((mt) => {
                const active = me.model_types.includes(mt);
                return (
                  <button
                    key={mt}
                    disabled={!editing}
                    onClick={() => {
                      if (!editing) return;
                      const cur = editing.model_types ?? me.model_types;
                      onEditChange({ model_types: active ? cur.filter((x) => x !== mt) : [...cur, mt] });
                    }}
                    className={cn(
                      "px-2.5 py-0.5 text-xs font-medium rounded-full border transition-all",
                      active ? "bg-accent-subtle text-accent border-accent" : "bg-secondary text-muted border-border",
                      !editing && "opacity-60 cursor-default",
                    )}
                  >
                    {t(`modelTypeLabels.${mt}`, { defaultValue: mt })}
                  </button>
                );
              })}
            </div>
          </div>

          {testResult && <div className="p-2 rounded bg-card border border-border text-xs text-foreground break-all">{testResult}</div>}
        </div>
      )}
    </div>
  );
}
