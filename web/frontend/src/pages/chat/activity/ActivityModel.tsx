import { AlertCircle, Brain, Loader2 } from "lucide-react";
import { useTranslation } from "react-i18next";
import type { ActivityEntry } from "@/lib/types/activity";
import { useNow } from "@/hooks/useNow";
import { formatElapsedCompact } from "@/lib/format";

/** 模型调用耗时与供应商实际报告的用量。 */
export function ActivityModel({ entry }: { entry: Extract<ActivityEntry, { kind: "model" }> }) {
  const { t } = useTranslation("workbench");
  const running = entry.status === "running";
  const now = useNow(running);
  const usage = entry.usage;
  const input = usage?.total_input_tokens;
  const cached = usage?.cache_read_input_tokens;
  const rate = usage?.cache_observable && input != null && input > 0 && cached != null
    ? Math.min(100, Math.max(0, Math.round(cached / input * 100))) : null;
  return <div className="activity-model">
    <span>{running ? <Loader2 size={12} className="animate-spin" /> : entry.status === "error" ? <AlertCircle size={12} className="text-warn" /> : <Brain size={12} />}{entry.name}</span>
    <span>{formatElapsedCompact(Math.max(0, running ? now - entry.ts * 1000 : entry.duration_ms ?? 0))}</span>
    {input != null && <span>{t("activity.modelUsage", { input: input.toLocaleString(), output: (usage?.completion_tokens ?? 0).toLocaleString() })}</span>}
    {!running && <span title={t("activity.cacheHint")}>{rate != null ? t("activity.modelCache", { percent: rate }) : t("activity.cacheUnknown")}</span>}
    {entry.error && <p role="alert">{entry.error}</p>}
  </div>;
}
