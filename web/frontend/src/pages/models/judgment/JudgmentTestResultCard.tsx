import { useTranslation } from "react-i18next";
import type { JudgmentTestResult } from "@/lib/types";
import { Badge } from "@/components/ui";

/** 通道测试执行结果卡片。 */
export function JudgmentTestResultCard({ result }: { result: JudgmentTestResult }) {
  const { t } = useTranslation("models");
  const speed = <div className="space-y-1 text-xs leading-relaxed">
    {result.slow && <p role="status" className="rounded-lg border border-warn/30 bg-warn/10 p-2.5 text-warn">{t("judgment.slow", { ms: result.latency_ms, threshold: result.slow_after_ms })}</p>}
    {result.source === "typesafe" && result.latency_ms >= result.gate_budget_ms && <p className="text-warn">{t("judgment.overBudget", { ms: result.gate_budget_ms })}</p>}
    <p className="text-muted">{t("judgment.speedHint")}</p>
  </div>;
  if (!result.ok) {
    return (
      <div className="rounded-md border border-danger/30 bg-danger-subtle p-3 text-sm">
        <div className="text-danger font-medium">{t("judgment.testFailed")}</div>
        <div className="text-xs text-muted mt-1">
          {result.error}
          {result.cause ? ` (${result.cause})` : ""}
          {` · ${result.latency_ms}ms`}
        </div>
        {speed}
      </div>
    );
  }
  return (
    <div className="rounded-md border border-border bg-card p-3 space-y-2">
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <Badge variant={result.source === "typesafe" ? "ok" : "warn"}>
          {result.source === "typesafe" ? t("judgment.sourceTypesafe") : t("judgment.sourceFallback")}
        </Badge>
        {result.model && <span className="text-muted font-mono">{result.model}</span>}
        <span className="text-muted">{result.latency_ms}ms</span>
        {result.usage && (
          <span className="text-muted">
            {t("judgment.tokens", { input: result.usage.input_tokens, output: result.usage.output_tokens })}
          </span>
        )}
      </div>
      {speed}
      <pre className="text-xs font-mono text-foreground bg-bg rounded-md p-3 overflow-x-auto whitespace-pre-wrap">
        {JSON.stringify(result.answers, null, 2)}
      </pre>
      {result.missing && result.missing.length > 0 && (
        <div className="text-xs text-warn">{t("judgment.missing", { ids: result.missing.join(", ") })}</div>
      )}
    </div>
  );
}
