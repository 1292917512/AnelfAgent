import { useTranslation } from "react-i18next";
import { Textarea } from "@/components/ui";
import { cn } from "@/lib/utils";

export function JsonSection({
  label,
  hint,
  enabled,
  onEnabledChange,
  value,
  onChange,
  error,
}: {
  label: string;
  hint: string;
  enabled: boolean;
  onEnabledChange: (v: boolean) => void;
  value: string;
  onChange: (v: string) => void;
  error?: string;
}) {
  const { t } = useTranslation("models");
  return (
    <div className={cn("rounded-md border p-3 space-y-2", enabled ? "border-accent2/50" : "border-border")}>
      <div className="flex items-center justify-between">
        <span className="text-xs font-medium text-muted" title={hint}>{label}</span>
        <label className="flex items-center gap-1.5 cursor-pointer text-xs text-muted">
          <input
            type="checkbox"
            checked={enabled}
            onChange={(e) => onEnabledChange(e.target.checked)}
            className="accent-accent2 w-3.5 h-3.5"
          />
          {t("enableLabel")}
        </label>
      </div>
      {enabled && (
        <>
          <Textarea
            aria-label={label}
            aria-invalid={!!error}
            rows={4}
            value={value}
            onChange={(e) => onChange(e.target.value)}
            className={cn("font-mono text-xs", error && "border-danger")}
            spellCheck={false}
          />
          <p className={cn("text-[11px]", error ? "text-danger" : "text-muted")}>
            {error ?? hint}
          </p>
        </>
      )}
    </div>
  );
}
