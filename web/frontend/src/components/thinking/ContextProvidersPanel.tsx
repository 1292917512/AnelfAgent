import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { contextApi } from "@/lib/api";
import { ProviderContent } from "@/components/common/ProviderContent";
import { PageSkeleton, QueryError } from "@/components/common/AsyncState";
import { cn } from "@/lib/utils";

function budgetColor(ratio: number): string {
  if (ratio >= 0.9) return "bg-danger";
  if (ratio >= 0.7) return "bg-warn";
  return "bg-ok";
}

export function ContextProvidersPanel() {
  const { t } = useTranslation("thinking");
  const [expanded, setExpanded] = useState<string | null>(null);
  const { data, error, isPending, refetch } = useQuery({
    queryKey: ["context-providers"],
    queryFn: () => contextApi.providers().then((r) => r.data),
    refetchInterval: 3000,
    throwOnError: false,
  });

  if (isPending) return <div className="p-3"><PageSkeleton /></div>;
  if (error) return <QueryError error={error} retry={() => void refetch()} />;
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

        {/* Provider 列表（点击展开最近一次注入正文） */}
        {data.providers.length === 0 ? (
          <p className="text-xs text-muted py-4 text-center">
            {t("contextProviders.empty")}
          </p>
        ) : (
          <div className="space-y-1.5">
            {data.providers.map((p) => {
              const open = expanded === p.name;
              return (
                <div
                  key={p.name}
                  className="rounded-lg bg-elevated border border-border transition-colors hover:border-border-strong"
                >
                  <button type="button" aria-expanded={open} onClick={() => setExpanded(open ? null : p.name)} className="w-full py-2 px-2.5 text-left">
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
                  </button>
                  {open && (
                    <div className="px-3.5 pb-2">
                      <ProviderContent provider={p} />
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
