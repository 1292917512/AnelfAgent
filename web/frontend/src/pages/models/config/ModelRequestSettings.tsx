import { useState } from "react";
import { ChevronRight } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Input } from "@/components/ui";
import { cn } from "@/lib/utils";
import { JsonSection } from "./ModelJsonSection";
import type { ModelEditorState } from "./useModelEditor";

export function ModelRequestSettings({ editor }: { editor: ModelEditorState }) {
  const { t } = useTranslation("models");
  const [expanded, setExpanded] = useState(false);
  const { draft, patch, fieldErrors, jsonDrafts, setJsonDrafts, jsonEnabled, setJsonEnabled, jsonErrors, setJsonErrors,
    builtinToolsText, setBuiltinToolsText, builtinToolsError, setBuiltinToolsError } = editor;
  const fields = ["extra_body", "extra_headers", "request_params"] as const;
  const hasError = !!(builtinToolsError || fieldErrors.temperature || fieldErrors.top_p || fields.some((field) => jsonErrors[field]));
  const open = expanded || hasError;
  return <section className="border-t border-border pt-4">
    <button type="button" aria-expanded={open} className="flex min-h-11 w-full items-center gap-3 rounded-lg text-left"
      onClick={() => setExpanded(!expanded)}>
      <ChevronRight size={16} className={cn("shrink-0 text-muted transition-transform", open && "rotate-90")} />
      <span className="min-w-0">
        <span className="block text-sm font-semibold text-heading">{t("editorAdvanced")}</span>
        <span className="mt-1 block text-xs text-muted">{t("editorAdvancedHint")}</span>
      </span>
    </button>
    {open && <div className="mt-4 space-y-4">
      <div className="grid gap-4 sm:grid-cols-2">
        {(["temperature", "top_p"] as const).map((field) => <div key={field} className="space-y-1.5">
          <p className="text-xs font-medium text-muted">{field}</p>
          <Input type="number" step="any" min={0} max={field === "temperature" ? 2 : 1} aria-label={field}
            aria-invalid={!!fieldErrors[field]} value={draft[field] ?? ""} placeholder={t("modelFields.temperatureHint")}
            onChange={(event) => patch({ [field]: event.target.value === "" ? null : Number(event.target.value) })} />
          {fieldErrors[field] && <p role="alert" className="text-xs text-danger">{t(fieldErrors[field])}</p>}
        </div>)}
      </div>
      <div className="space-y-1.5">
        <p className="text-xs font-medium text-muted">{t("modelFields.builtin_tools")}</p>
        <Input aria-label={t("modelFields.builtin_tools")} aria-invalid={!!builtinToolsError} value={builtinToolsText}
          placeholder="web_search, code_interpreter" spellCheck={false} className="font-mono text-xs"
          onChange={(event) => { setBuiltinToolsText(event.target.value); setBuiltinToolsError(undefined); }} />
        <p className={cn("text-xs leading-relaxed", builtinToolsError ? "text-danger" : "text-muted")}>{builtinToolsError ?? t("modelFields.builtin_toolsHint")}</p>
      </div>
      {fields.map((field) => <JsonSection key={field} label={t(`modelFields.${field}`)} hint={t(`modelFields.${field}Hint`)}
        enabled={jsonEnabled[field]} onEnabledChange={(value) => setJsonEnabled((prev) => ({ ...prev, [field]: value }))}
        value={jsonDrafts[field]} onChange={(value) => { setJsonDrafts((prev) => ({ ...prev, [field]: value })); setJsonErrors((prev) => ({ ...prev, [field]: undefined })); }}
        error={jsonErrors[field]} />)}
    </div>}
  </section>;
}
