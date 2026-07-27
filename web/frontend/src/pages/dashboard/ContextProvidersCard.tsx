import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { statusApi } from "@/lib/api";
import { Card } from "@/components/common/Card";
import type { ContextProviderStatus } from "@/lib/types";

function budgetColor(ratio: number): string {
  if (ratio >= 0.9) return "bg-danger";
  if (ratio >= 0.7) return "bg-warn";
  return "bg-ok";
}

function budgetTextColor(ratio: number): string {
  if (ratio >= 0.9) return "text-danger";
  if (ratio >= 0.7) return "text-warn";
  return "text-ok";
}

export function ContextProvidersCard() {
  const { t } = useTranslation("dashboard");
  const { data } = useQuery({
    queryKey: ["context-providers"],
    queryFn: () => statusApi.contextProviders().then((r) => r.data as ContextProviderStatus),
    refetchInterval: 3000,
  });

  if (!data || data.providers.length === 0) return null;

  const ratio = data.total_budget > 0 ? data.current_used / data.total_budget : 0;
  const peakRatio = data.total_budget > 0 ? data.peak_used / data.total_budget : 0;

  return (
    <Card title={t("contextProviders.title")} subtitle={t("contextProviders.subtitle")}>
      <div className="space-y-4">
        {/* 预算进度条 */}
        <div>
          <div className="flex items-center justify-between text-xs mb-1.5">
            <span className="text-muted">{t("contextProviders.budget")}</span>
            <span className={`font-mono font-medium ${budgetTextColor(ratio)}`}>
              {data.current_used} / {data.total_budget} tokens
            </span>
          </div>
          <div className="h-2 rounded-full bg-elevated overflow-hidden">
            <div
              className={`h-full rounded-full transition-all duration-500 ${budgetColor(ratio)}`}
              style={{ width: `${Math.min(ratio * 100, 100)}%` }}
            />
          </div>
          <div className="flex items-center justify-between text-[10px] text-muted mt-1">
            <span>{t("contextProviders.staticEstimate")}: {data.static_estimate}</span>
            <span>{t("contextProviders.peak")}: {data.peak_used} ({Math.round(peakRatio * 100)}%)</span>
          </div>
        </div>

        {/* Provider 列表 */}
        <div className="space-y-2">
          {data.providers.map((p) => (
            <div
              key={p.name}
              className="flex items-center gap-3 py-1.5 px-3 rounded-sm bg-elevated border border-border"
            >
              {/* ready 状态 */}
              <span
                className={`w-2 h-2 rounded-full flex-shrink-0 ${p.ready ? "bg-ok" : "bg-warn animate-pulse"}`}
                title={p.ready ? t("contextProviders.ready") : t("contextProviders.notReady")}
              />
              {/* 名称 + 描述 */}
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2">
                  <span className="text-xs font-medium text-foreground truncate">{p.name}</span>
                  {p.description && (
                    <span className="text-[10px] text-muted truncate hidden sm:inline">{p.description}</span>
                  )}
                </div>
                {p.last_error && (
                  <span className="text-[10px] text-danger truncate block">{p.last_error}</span>
                )}
              </div>
              {/* 指标 */}
              <div className="flex items-center gap-3 text-[10px] font-mono text-muted flex-shrink-0">
                <span title={t("contextProviders.tokens")}>{p.tokens}t</span>
                <span title={t("contextProviders.bytes")}>{p.bytes}B</span>
                <span title={t("contextProviders.costMs")}>{p.cost_ms.toFixed(1)}ms</span>
                <span title={t("contextProviders.callCount")}>×{p.call_count}</span>
              </div>
            </div>
          ))}
        </div>
      </div>
    </Card>
  );
}
