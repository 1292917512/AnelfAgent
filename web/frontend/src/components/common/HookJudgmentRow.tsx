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

/** 单行展示判断任务、结论和耗时，依据与诊断仅在展开时挂载。 */
export function HookJudgmentRow({ judgment }: { judgment: HookJudgment }) {
  const { t } = useTranslation("settings");
  const [expanded, setExpanded] = useState(false);
  const detailsId = useId();
  const running = judgment.status === "running";
  const routing = judgment.policy === "routing";
  const selection = routing && judgment.status === "proceed" && judgment.selection
    ? t(`hookJudgment.levels.${judgment.selection}`, { defaultValue: judgment.selection }) : "";
  const now = useNow(running);
  const elapsed = running ? Math.max(0, now - judgment.started_at * 1000) : judgment.duration_ms;
  const slow = judgment.status !== "cancelled" && (judgment.slow || (running && judgment.slow_after_ms > 0 && elapsed >= judgment.slow_after_ms));
  const duration = elapsed < 1000 ? `${Math.round(elapsed)}ms` : `${(elapsed / 1000).toFixed(1)}s`;
  return (
    <div className={cn("min-w-0 overflow-hidden rounded-md border border-border/60", slow && "border-warn/40")}
      data-judgment={judgment.id}>
      <button type="button" aria-expanded={expanded} aria-controls={detailsId}
        aria-label={`${t("hookJudgment.title", { provider: judgment.provider })}: ${judgment.task}`}
        title={judgment.task}
        onClick={() => setExpanded(!expanded)}
        className="flex min-h-10 w-full items-center gap-2 px-2.5 py-1.5 text-left text-xs hover:bg-elevated/50">
        {running ? <Loader2 size={13} className="shrink-0 animate-spin text-accent motion-reduce:animate-none" /> : <Gauge size={13} className="shrink-0 text-muted" />}
        <span className="shrink-0 font-medium text-heading">{judgment.provider}</span>
        <span className="min-w-0 flex-1 truncate text-foreground">{judgment.task}</span>
        <Badge variant={variants[judgment.status]}>{selection || t(`hookJudgment.status.${judgment.status}`)}</Badge>
        {slow && <TriangleAlert size={13} className="shrink-0 text-warn" aria-label={t("hookJudgment.slow", { ms: judgment.slow_after_ms })} />}
        <span className="shrink-0 tabular-nums text-muted">{duration}</span>
        <ChevronDown size={13} className={cn("shrink-0 text-muted transition-transform motion-reduce:transition-none", expanded && "rotate-180")} />
      </button>
      {expanded && <div id={detailsId} className="space-y-3 border-t border-accent/15 px-3 py-3 text-xs">
        <p className="break-words font-medium text-heading">{judgment.task}</p>
        {slow && <div role="status" className="flex items-start gap-2 rounded-md bg-warn/10 p-2 text-warn">
          <TriangleAlert size={14} className="mt-0.5 shrink-0" />
          <span className="min-w-0 flex-1 leading-relaxed">{t("hookJudgment.slow", { ms: judgment.slow_after_ms })}
            <Link to="/models?tab=judgment" className="ml-1 underline underline-offset-2">{t("hookJudgment.testSpeed")}</Link>
          </span>
        </div>}
        {judgment.probability != null && <p className="text-muted">{t(routing ? "hookJudgment.routingProbability" : "hookJudgment.probability", { value: percent(judgment.probability) })}</p>}
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
        <p className="leading-relaxed text-muted">{t(routing ? "hookJudgment.routingPolicy" : judgment.policy === "selective" ? "hookJudgment.selectivePolicy" : "hookJudgment.policy", { threshold: percent(judgment.threshold), confidence: percent(1 - judgment.threshold) })}</p>
        {judgment.input_truncated && <p className="text-warn">{t(routing ? "hookJudgment.routingExcerpt" : "hookJudgment.truncated")}</p>}
      </div>}
    </div>
  );
}
