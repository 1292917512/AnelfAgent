import { useId, useState } from "react";
import { useTranslation } from "react-i18next";
import { ChevronDown, Gauge, Loader2, TriangleAlert } from "lucide-react";
import { Link } from "react-router-dom";
import { Badge, type BadgeVariant } from "@/components/ui";
import { useNow } from "@/hooks/useNow";
import type { HookJudgment } from "@/lib/types/hooks";
import { cn } from "@/lib/utils";

const variants: Record<HookJudgment["status"], BadgeVariant> = {
  running: "info", proceed: "ok", skip: "info", fallback: "warn", cancelled: "neutral",
};
const percent = (value: number) => `${Math.round(value * 100)}%`;

/** 判断摘要常驻，依据、策略阈值与实测用量按需展开。 */
export function HookJudgmentRow({ judgment }: { judgment: HookJudgment }) {
  const { t } = useTranslation("settings");
  const [expanded, setExpanded] = useState(false);
  const detailsId = useId();
  const running = judgment.status === "running";
  const now = useNow(running);
  const elapsed = running ? Math.max(0, now - judgment.started_at * 1000) : judgment.duration_ms;
  const slow = judgment.status !== "cancelled" && (judgment.slow || (running && judgment.slow_after_ms > 0 && elapsed >= judgment.slow_after_ms));
  const duration = elapsed < 1000 ? `${Math.round(elapsed)}ms` : `${(elapsed / 1000).toFixed(1)}s`;
  return (
    <div className={cn("overflow-hidden rounded-xl border border-accent/25 bg-accent/5", slow && "border-warn/40")}
      data-judgment={judgment.id}>
      <button type="button" aria-expanded={expanded} aria-controls={detailsId}
        onClick={() => setExpanded(!expanded)}
        className="flex w-full items-start gap-3 px-3 py-3 text-left">
        <span className="mt-0.5 rounded-lg bg-accent/10 p-2 text-accent">
          {running ? <Loader2 size={16} className="animate-spin motion-reduce:animate-none" /> : <Gauge size={16} />}
        </span>
        <span className="min-w-0 flex-1 space-y-1.5">
          <span className="flex flex-wrap items-center gap-x-2 gap-y-1">
            <span className="text-sm font-semibold text-heading">{t("hookJudgment.title", { provider: judgment.provider })}</span>
            <Badge variant={variants[judgment.status]}>{t(`hookJudgment.status.${judgment.status}`)}</Badge>
            <span className="text-xs tabular-nums text-muted">{duration}</span>
          </span>
          <span className="block break-words text-xs text-foreground">{judgment.task}</span>
          {judgment.probability != null && <span className="block text-xs text-muted">
            {t("hookJudgment.probability", { value: percent(judgment.probability) })}
          </span>}
        </span>
        <ChevronDown size={14} className={cn("mt-2 shrink-0 text-muted transition-transform motion-reduce:transition-none", expanded && "rotate-180")} />
      </button>
      {slow && <div role="status" className="mx-3 mb-3 flex items-start gap-2 rounded-lg bg-warn/10 p-2.5 text-xs text-warn">
        <TriangleAlert size={14} className="mt-0.5 shrink-0" />
        <span className="min-w-0 flex-1 leading-relaxed">{t("hookJudgment.slow", { ms: judgment.slow_after_ms })}
          <Link to="/models?tab=judgment" className="ml-1 underline underline-offset-2">{t("hookJudgment.testSpeed")}</Link>
        </span>
      </div>}
      {expanded && <div id={detailsId} className="space-y-3 border-t border-accent/15 px-3 py-3 text-xs">
        <p className="break-words leading-relaxed text-foreground">{judgment.reason || t("hookJudgment.evaluating")}</p>
        {judgment.basis && <p className="break-words leading-relaxed text-muted">
          {t("hookJudgment.basis", { value: judgment.basis })}
          {judgment.basis_probability != null && ` · ${percent(judgment.basis_probability)}`}
        </p>}
        <dl className="grid grid-cols-1 gap-x-6 gap-y-2 text-muted sm:grid-cols-2">
          <div><dt>{t("hookJudgment.model")}</dt><dd className="mt-0.5 break-all font-mono text-foreground">{judgment.model || "—"}</dd></div>
          <div><dt>{t("hookJudgment.input")}</dt><dd className="mt-0.5 tabular-nums text-foreground">{t("hookJudgment.chars", { count: judgment.input_chars })}</dd></div>
          {(judgment.input_tokens > 0 || judgment.output_tokens > 0) && <div className="sm:col-span-2">
            <dt>{t("hookJudgment.usage")}</dt><dd className="mt-0.5 tabular-nums text-foreground">{t("hookJudgment.tokens", { input: judgment.input_tokens, output: judgment.output_tokens })}</dd>
          </div>}
        </dl>
        <p className="leading-relaxed text-muted">{t("hookJudgment.policy", { threshold: percent(judgment.threshold), confidence: percent(1 - judgment.threshold) })}</p>
        {judgment.input_truncated && <p className="text-warn">{t("hookJudgment.truncated")}</p>}
      </div>}
    </div>
  );
}
