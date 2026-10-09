import { useTranslation } from "react-i18next";
import { AlertTriangle, CheckCircle2, Loader2, XCircle } from "lucide-react";
import { cn } from "@/lib/utils";
import type { ModelTestState } from "./useModelEditor";

export function ModelTestResult({ test, stale }: { test: ModelTestState; stale: boolean }) {
  const { t } = useTranslation("models");
  return <>
        {test.status !== "idle" && (
          <div className={cn(
            "rounded-md border p-3 text-xs space-y-1.5",
            test.status === "running" && "border-border bg-elevated",
            test.status === "success" && !stale && "border-[rgba(34,197,94,0.4)] bg-[rgba(34,197,94,0.06)]",
            test.status === "error" && !stale && "border-danger/40 bg-danger/5",
            stale && "border-[rgba(234,179,8,0.4)] bg-[rgba(234,179,8,0.06)]",
          )}>
            <div className="flex items-center gap-1.5 font-medium">
              {test.status === "running" && (
                <><Loader2 size={13} className="animate-spin text-muted" /><span className="text-muted">{t("testing")}</span></>
              )}
              {test.status === "success" && !stale && (
                <><CheckCircle2 size={13} className="text-ok" /><span className="text-ok">{t("testPassed")}</span></>
              )}
              {test.status === "error" && !stale && (
                <><XCircle size={13} className="text-danger" /><span className="text-danger">{t("testFailed")}</span></>
              )}
              {stale && (
                <><AlertTriangle size={13} className="text-warn" /><span className="text-warn">{t("testStale")}</span></>
              )}
            </div>
            {!stale && test.result?.ok && (
              <>
                <div className="flex flex-wrap gap-x-4 gap-y-1 text-foreground">
                  <span>{t("firstTokenLatency")}: {test.result.ttft_ms}ms</span>
                  <span>{t("totalDuration")}: {test.result.total_ms}ms</span>
                  <span>
                    {t("outputTokensLabel")}: {test.result.output_tokens}
                    {test.result.tokens_estimated && (
                      <span className="text-warn"> ({t("estimatedMark")})</span>
                    )}
                  </span>
                </div>
                {test.result.reply_preview && (
                  <p className="text-muted font-mono break-all">{test.result.reply_preview}</p>
                )}
              </>
            )}
            {!stale && test.result?.error && (
              <p className="text-danger break-all">{test.result.error}</p>
            )}
          </div>
        )}
  </>;
}
