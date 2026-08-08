import { useTranslation } from "react-i18next";
import { ChevronDown, ChevronRight, Pencil, Scan, Trash2, Wand2 } from "lucide-react";
import type { ModelConfig } from "@/lib/types";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui";
import { ModelBadges } from "./shared";

/** 单个模型卡片：头部徽标 + 展开只读摘要；编辑在 ModelEditorDialog 中进行 */
export function ModelCard({
  model,
  expanded,
  onToggle,
  onEdit,
  onProbe,
  onAutoConfig,
  onRemove,
  testResult,
  isPending,
}: {
  model: ModelConfig;
  expanded: boolean;
  onToggle: () => void;
  onEdit: () => void;
  onProbe: () => void;
  onAutoConfig: () => void;
  onRemove: () => void;
  testResult: string;
  isPending: boolean;
}) {
  const { t } = useTranslation(["models", "common"]);

  const details: Array<[string, string]> = [
    [t("modelFields.context_window"), model.context_window ? String(model.context_window) : "—"],
    [t("modelFields.max_tokens"), model.max_tokens != null ? String(model.max_tokens) : "—"],
    ["temperature", model.temperature != null ? String(model.temperature) : "—"],
    [t("endpointLabel"), t(`chatProtocol.${model.chat_protocol ?? "chat_completions"}`)],
    [
      t("reasoningEffort"),
      model.supports_reasoning
        ? model.reasoning_effort
          ? t(`effort${model.reasoning_effort.charAt(0).toUpperCase()}${model.reasoning_effort.slice(1)}`)
          : t("effortInherit")
        : "—",
    ],
  ];
  const extraCount =
    Object.keys(model.extra_body ?? {}).length +
    Object.keys(model.request_params ?? {}).length +
    Object.keys(model.extra_headers ?? {}).length;

  return (
    <div className={cn(
      "rounded-md border transition-all",
      expanded ? "border-accent2 bg-elevated" : "border-border bg-elevated hover:border-border-strong",
      !model.enabled && "opacity-60",
    )}>
      <div className="flex items-center justify-between gap-2 p-3 cursor-pointer" onClick={onToggle}>
        <div className="flex items-center gap-2 min-w-0 flex-wrap">
          {expanded ? <ChevronDown size={14} className="text-accent2 shrink-0" /> : <ChevronRight size={14} className="text-muted shrink-0" />}
          <span className="text-sm font-medium text-heading truncate">{model.id}</span>
          <span className="text-xs text-muted truncate hidden sm:inline">{model.model}</span>
          {!model.enabled && (
            <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-secondary text-muted border border-border">
              {t("disabled")}
            </span>
          )}
          <ModelBadges model={model} />
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
            <Button variant="primary" size="sm" onClick={onEdit}>
              <Pencil size={12} /> {t("common:edit")}
            </Button>
            <Button variant="secondary" size="sm" onClick={onProbe} loading={isPending}>
              <Scan size={12} /> {t("probeCapability")}
            </Button>
            <Button variant="secondary" size="sm" onClick={onAutoConfig} className="border-accent text-accent hover:bg-accent-subtle">
              <Wand2 size={12} /> {t("autoConfig")}
            </Button>
          </div>

          <div className="grid grid-cols-2 md:grid-cols-3 gap-x-4 gap-y-1.5">
            {details.map(([k, v]) => (
              <p key={k} className="text-xs truncate">
                <span className="text-muted">{k}: </span>
                <span className="text-foreground">{v}</span>
              </p>
            ))}
            <p className="text-xs truncate">
              <span className="text-muted">{t("extendedParams")}: </span>
              <span className="text-foreground">
                {extraCount > 0 ? t("extendedParamsCount", { count: extraCount }) : "—"}
              </span>
            </p>
          </div>

          {testResult && <div className="p-2 rounded bg-card border border-border text-xs text-foreground break-all">{testResult}</div>}
        </div>
      )}
    </div>
  );
}
