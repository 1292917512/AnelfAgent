import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useQuery } from "@tanstack/react-query";
import { ChevronRight } from "lucide-react";
import { contextApi } from "@/lib/api";
import { Card } from "@/components/common/Card";
import { StatusDot } from "@/components/common/StatusDot";
import { ProviderContent } from "@/components/common/ProviderContent";
import { cn } from "@/lib/utils";
import { PageSkeleton, QueryError } from "@/components/common/AsyncState";

export function ProvidersPanel() {
  const { t } = useTranslation("context");
  const [expanded, setExpanded] = useState<string | null>(null);
  const { data, error, isPending, refetch } = useQuery({
    queryKey: ["context-providers"],
    queryFn: () => contextApi.providers().then((r) => r.data),
    refetchInterval: 3000,
    retry: false,
    throwOnError: false,
  });

  if (isPending) return <PageSkeleton />;
  if (error) return <QueryError error={error} retry={() => void refetch()} />;
  if (!data) return null;

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

      {data.providers.length === 0 && <p className="py-8 text-center text-sm text-muted">{t("providers.empty")}</p>}
      <div className="space-y-2">
        {data.providers.map((p) => {
          const open = expanded === p.name;
          return (
            <div
              key={p.name}
              className={cn(
                "rounded-lg bg-card border border-border transition-colors hover:border-border-strong",
                p.active === false && "opacity-55",
              )}
            >
              <button type="button" aria-expanded={open} onClick={() => setExpanded(open ? null : p.name)} className="flex w-full flex-wrap items-center gap-3 py-3 px-3 text-left">
                <ChevronRight
                  className={cn(
                    "h-3.5 w-3.5 text-muted transition-transform flex-shrink-0",
                    open && "rotate-90",
                  )}
                />
                <StatusDot status={p.active === false ? "offline" : p.injecting ? "ok" : p.ready ? "warn" : "warn"} />
                <div className="flex-1 min-w-32">
                  <span className="text-xs font-medium text-foreground">{p.name}</span>
                  {p.group && <span className="ml-1.5 text-[10px] text-muted font-mono">{p.group}</span>}
                  {/* 注入状态徽标：区分"已注册未注入"（如直播模式关闭）与实际注入 */}
                  {p.active !== false && (
                    <span
                      className={cn(
                        "ml-1.5 text-[10px] px-1.5 py-0.5 rounded",
                        p.injecting ? "bg-ok-subtle text-ok" : "bg-border/40 text-muted",
                      )}
                      title={p.injecting && (p.injected_at ?? 0) > 0
                        ? `${t("providers.injectedAt")}: ${new Date((p.injected_at ?? 0) * 1000).toLocaleTimeString()}`
                        : undefined}
                    >
                      {p.injecting ? t("providers.injecting") : t("providers.notInjecting")}
                    </span>
                  )}
                  {p.active === false && (
                    <span className="ml-1.5 text-[10px] px-1.5 py-0.5 rounded bg-border/40 text-muted">
                      {t("providers.inactive")}
                    </span>
                  )}
                  {p.description && <p className="mt-1 text-xs leading-relaxed text-muted break-words">{p.description}</p>}
                  {p.last_error && <p className="mt-1 text-xs text-danger break-words">{p.last_error}</p>}
                </div>
                <div className="flex flex-wrap items-center gap-3 text-[11px] font-mono text-muted">
                  <span>{p.tokens}t</span>
                  <span>{p.bytes}B</span>
                  <span>{p.cost_ms.toFixed(1)}ms</span>
                  <span>×{p.call_count}</span>
                </div>
              </button>
              {open && (
                <div className="border-t border-border px-3 py-3">
                  <ProviderContent provider={p} />
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
