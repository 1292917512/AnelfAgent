import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { contextApi } from "@/lib/api";
import { cn } from "@/lib/utils";
import type { ContextProviderStatus } from "@/lib/types";

function budgetColor(ratio: number): string {
  if (ratio >= 0.9) return "bg-danger";
  if (ratio >= 0.7) return "bg-warn";
  return "bg-ok";
}

export function ContextProvidersPanel() {
  const { t } = useTranslation("thinking");
  const { data } = useQuery({
    queryKey: ["context-providers"],
    queryFn: () => contextApi.providers().then((r) => r.data as ContextProviderStatus),
    refetchInterval: 3000,
  });

  if (!data) return null;

  const ratio = data.total_budget > 0 ? data.current_used / data.total_budget : 0;

  return (
    <div className="h-full flex flex-col">
      <div className="px-3 py-2 border-b border-border">
        <span className="text-xs font-semibold text-heading uppercase tracking-wider">
          {t("contextProviders.title")}
        </span>
      </div>

      <div className="flex-1 overflow-y-auto p-3 space-y-3">
        {/* 预算进度条 */}
        <div>
          <div className="flex items-center justify-between text-[10px] mb-1">
            <span className="text-muted">{t("contextProviders.budget")}</span>
            <span className="font-mono text-muted">
              {data.current_used}/{data.total_budget}
            </span>
          </div>
          <div className="h-1.5 rounded-full bg-elevated overflow-hidden">
            <div
              className={cn("h-full rounded-full transition-all duration-500", budgetColor(ratio))}
              style={{ width: `${Math.min(ratio * 100, 100)}%` }}
            />
          </div>
          <div className="text-[10px] text-muted mt-1">
            {t("contextProviders.peak")}: {data.peak_used}
          </div>
        </div>

        {/* Provider 列表 */}
        {data.providers.length === 0 ? (
          <p className="text-xs text-muted py-4 text-center">
            {t("contextProviders.empty")}
          </p>
        ) : (
          <div className="space-y-1.5">
            {data.providers.map((p) => (
              <div
                key={p.name}
                className="py-1.5 px-2.5 rounded-sm bg-elevated border border-border"
              >
                <div className="flex items-center gap-2">
                  <span
                    className={cn(
                      "w-1.5 h-1.5 rounded-full flex-shrink-0",
                      p.injecting ? "bg-ok" : p.ready ? "bg-warn" : "bg-warn animate-pulse",
                    )}
                  />
                  <span className="text-[11px] font-medium text-foreground truncate flex-1">
                    {p.name}
                  </span>
                  {/* 注入状态徽标：未注入（模式关闭/无内容）时弱化展示 */}
                  <span
                    className={cn(
                      "text-[9px] px-1 py-px rounded flex-shrink-0",
                      p.injecting ? "bg-ok-subtle text-ok" : "bg-border/40 text-muted",
                    )}
                  >
                    {p.injecting
                      ? t("contextProviders.injecting")
                      : t("contextProviders.notInjecting")}
                  </span>
                  <span className="text-[10px] font-mono text-muted flex-shrink-0">
                    {p.tokens}t · {p.cost_ms.toFixed(0)}ms
                  </span>
                </div>
                {p.description && (
                  <p className="text-[10px] text-muted mt-0.5 truncate pl-3.5">{p.description}</p>
                )}
                {p.last_error && (
                  <p className="text-[10px] text-danger mt-0.5 truncate pl-3.5">{p.last_error}</p>
                )}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
