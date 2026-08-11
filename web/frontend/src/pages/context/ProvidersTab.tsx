import { useTranslation } from "react-i18next";
import { useQuery } from "@tanstack/react-query";
import { contextApi } from "@/lib/api";
import { Card } from "@/components/common/Card";
import { StatusDot } from "@/components/common/StatusDot";
import { cn } from "@/lib/utils";
import type { ContextProviderStatus } from "@/lib/types";

export function ProvidersTab() {
  const { t } = useTranslation("context");
  const { data } = useQuery({
    queryKey: ["context-providers"],
    queryFn: () => contextApi.providers().then((r) => r.data as ContextProviderStatus),
    refetchInterval: 3000,
  });

  if (!data || data.providers.length === 0) {
    return <p className="text-sm text-muted py-8 text-center">{t("providers.empty")}</p>;
  }

  const ratio = data.total_budget > 0 ? data.current_used / data.total_budget : 0;

  return (
    <div className="space-y-4">
      <Card title={t("providers.budget")}>
        <div className="flex items-center justify-between text-xs mb-1.5">
          <span className="text-muted">{t("providers.used")}</span>
          <span className={cn("font-mono font-medium", ratio >= 0.9 ? "text-danger" : ratio >= 0.7 ? "text-warn" : "text-ok")}>
            {data.current_used} / {data.total_budget}
          </span>
        </div>
        <div className="h-2 rounded-full bg-elevated overflow-hidden">
          <div
            className={cn("h-full rounded-full transition-all duration-500", ratio >= 0.9 ? "bg-danger" : ratio >= 0.7 ? "bg-warn" : "bg-ok")}
            style={{ width: `${Math.min(ratio * 100, 100)}%` }}
          />
        </div>
        <div className="flex items-center justify-between text-[10px] text-muted mt-1">
          <span>{t("providers.staticEstimate")}: {data.static_estimate}</span>
          <span>{t("providers.peak")}: {data.peak_used}</span>
        </div>
        {(data.collected_at ?? 0) > 0 && (
          <p className="text-[10px] text-muted mt-1 opacity-70">
            {t("providers.lastCollect", {
              scope: data.scope || "-",
              time: new Date((data.collected_at ?? 0) * 1000).toLocaleTimeString(),
            })}
          </p>
        )}
      </Card>

      <div className="space-y-2">
        {data.providers.map((p) => (
          <div key={p.name} className="flex items-center gap-3 py-2 px-3 rounded-lg bg-elevated border border-border">
            <StatusDot status={p.ready ? "ok" : "warn"} />
            <div className="flex-1 min-w-0">
              <span className="text-xs font-medium text-foreground">{p.name}</span>
              {p.description && <p className="text-[10px] text-muted truncate">{p.description}</p>}
              {p.last_error && <p className="text-[10px] text-danger truncate">{p.last_error}</p>}
            </div>
            <div className="flex items-center gap-3 text-[10px] font-mono text-muted flex-shrink-0">
              <span>{p.tokens}t</span>
              <span>{p.bytes}B</span>
              <span>{p.cost_ms.toFixed(1)}ms</span>
              <span>×{p.call_count}</span>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

// ======================================================================
// 主页面
// ======================================================================
