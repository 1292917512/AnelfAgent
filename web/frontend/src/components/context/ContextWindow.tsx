import { useState } from "react";
import { useTranslation } from "react-i18next";
import type { ContextSnapshotData } from "@/lib/types";
import { cn } from "@/lib/utils";
import { LAYER_BAR_COLORS } from "@/components/common/SnapshotBlocks";

export function ContextWindow({ snapshot }: { snapshot: ContextSnapshotData }) {
  const { t } = useTranslation("context");
  const [selected, setSelected] = useState<number | null>(null);
  const capacity = snapshot.model_context_window;
  const used = snapshot.estimated_tokens;
  const sectionTotal = snapshot.sections.reduce((sum, section) => sum + section.estimated_tokens, 0);
  const scale = Math.max(capacity, used, sectionTotal, 1);
  const other = Math.max(0, used - sectionTotal);
  const detail = selected === null ? undefined : snapshot.sections[selected];
  return <section className="rounded-xl border border-border bg-card p-4" aria-label={t("window.title")}>
    <div className="flex flex-wrap items-baseline justify-between gap-2">
      <h2 className="text-sm font-semibold text-heading">{t("window.title")}</h2>
      <p className="text-xs tabular-nums text-muted">
        <strong className="text-heading">~{used.toLocaleString()}</strong>
        {capacity > 0 && ` / ${capacity.toLocaleString()}`} tokens
      </p>
    </div>
    <p className="mt-1 text-xs leading-relaxed text-muted">{t("window.description")}</p>
    <div className="mt-4 flex h-4 overflow-hidden rounded-full bg-elevated" aria-hidden="true">
      {snapshot.sections.map((section, index) => <span key={`${section.layer}-${index}`} className={cn(LAYER_BAR_COLORS[section.layer] ?? "bg-muted", "border-r border-card/50")} style={{ width: `${section.estimated_tokens / scale * 100}%` }} />)}
      {other > 0 && <span className="bg-muted/40" style={{ width: `${other / scale * 100}%` }} />}
    </div>
    <div className="mt-3 flex flex-wrap gap-1">
      {snapshot.sections.map((section, index) => <button key={`${section.layer}-${index}`} aria-pressed={selected === index}
        onClick={() => setSelected(selected === index ? null : index)}
        className={cn("inline-flex min-h-9 items-center gap-2 rounded-lg px-2 text-xs transition-colors", selected === index ? "bg-accent-subtle text-accent" : "text-muted hover:bg-elevated hover:text-heading")}>
        <span className={cn("h-2 w-2 rounded-full", LAYER_BAR_COLORS[section.layer] ?? "bg-muted")} />
        {section.label}<span className="font-mono text-[10px]">{section.estimated_tokens.toLocaleString()}</span>
      </button>)}
      {other > 0 && <span className="inline-flex items-center px-2 text-xs text-muted">{t("window.other", { tokens: other.toLocaleString() })}</span>}
      {capacity > 0 && <span className="inline-flex items-center px-2 text-xs text-muted">{t("window.remaining", { tokens: Math.max(0, capacity - used).toLocaleString() })}</span>}
    </div>
    {detail && <p role="status" className="mt-2 border-t border-border pt-3 text-xs leading-relaxed text-muted">
      <strong className="text-heading">{detail.label}</strong> · {detail.count} {t("stats.messages")} · {detail.volatility_label ?? t("window.unknown")}
      {detail.stable_count != null && ` · ${t("window.stable", { count: detail.stable_count })}`}
    </p>}
    {capacity > 0 && used >= capacity * 0.9 && <p className="mt-3 text-xs text-warn">{t("window.pressure")}</p>}
  </section>;
}
