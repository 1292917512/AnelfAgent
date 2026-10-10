import { useState, useMemo } from "react";
import { useTranslation } from "react-i18next";
import { useQuery } from "@tanstack/react-query";
import { approvalsApi } from "@/lib/api";
import type { ApprovalHistoryItem } from "@/lib/types";
import { Badge, type BadgeVariant } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { EmptyState } from "@/components/ui/EmptyState";
import { LoadingBlock } from "@/components/ui/Spinner";
import { cn } from "@/lib/utils";
import {
  CheckCircle,
  XCircle,
  Clock,
  Ban,
  History,
  ChevronDown,
  ChevronUp,
  Search,
} from "lucide-react";
import type { TOptions } from "i18next";

type DecisionFilter = "all" | "guardian_approved" | "guardian_denied" | "guardian_bypass" | "denied" | "permission_error";

const DECISION_VARIANT: Record<string, BadgeVariant> = {
  guardian_approved: "ok",
  guardian_denied: "danger",
  guardian_bypass: "warn",
  permission_error: "danger",
  approved: "ok",
  denied: "danger",
  expired: "warn",
  cancelled: "neutral",
};

const DECISION_ICON: Record<string, typeof CheckCircle> = {
  guardian_approved: CheckCircle,
  guardian_denied: XCircle,
  guardian_bypass: Clock,
  permission_error: Ban,
  approved: CheckCircle,
  denied: XCircle,
  expired: Clock,
  cancelled: Ban,
};

const DECISION_ICON_COLOR: Record<string, string> = {
  guardian_approved: "text-ok",
  guardian_denied: "text-danger",
  guardian_bypass: "text-warn",
  permission_error: "text-danger",
  approved: "text-ok",
  denied: "text-danger",
  expired: "text-warn",
  cancelled: "text-muted",
};

function relativeTime(ts: number, t: (key: string, opts?: TOptions) => string): string {
  const diff = Math.max(0, Math.floor(Date.now() / 1000 - ts));
  if (diff < 60) return t("timeAgo.justNow");
  if (diff < 3600) return t("timeAgo.minutes", { count: Math.floor(diff / 60) });
  if (diff < 86400) return t("timeAgo.hours", { count: Math.floor(diff / 3600) });
  return t("timeAgo.days", { count: Math.floor(diff / 86400) });
}

export function ApprovalHistory() {
  const { t } = useTranslation("approvals");
  const [expandedId, setExpandedId] = useState<number | null>(null);
  const [filter, setFilter] = useState<DecisionFilter>("all");
  const [search, setSearch] = useState("");

  const [offset, setOffset] = useState(0);
  const { data, isLoading } = useQuery({
    queryKey: ["approvals", "history", offset],
    queryFn: () => approvalsApi.history(50, offset).then((r) => r.data),
    refetchInterval: offset === 0 ? 10000 : false,
  });

  const history: ApprovalHistoryItem[] = useMemo(
    () => data?.history ?? [],
    [data],
  );

  const counts = useMemo(() => {
    const c: Record<string, number> = { all: history.length };
    for (const item of history) c[item.outcome] = (c[item.outcome] ?? 0) + 1;
    return c;
  }, [history]);

  const filtered = history.filter((item) => {
    if (filter !== "all" && item.outcome !== filter) return false;
    if (search && !item.tool_name.toLowerCase().includes(search.toLowerCase())) return false;
    return true;
  });

  if (isLoading) {
    return <LoadingBlock label={t("loading")} />;
  }

  const FILTERS: DecisionFilter[] = ["all", "guardian_approved", "guardian_denied", "guardian_bypass", "denied", "permission_error"];

  return (
    <div className="space-y-3">
      {/* 过滤栏 */}
      <div className="flex flex-col sm:flex-row gap-2 sm:items-center">
        <div className="flex flex-wrap gap-1.5">
          {FILTERS.map((f) => (
            <button
              key={f}
              onClick={() => setFilter(f)}
              className={cn(
                "px-2.5 py-1 rounded-full text-xs font-medium border transition-colors",
                filter === f
                  ? "bg-accent-subtle text-accent border-accent"
                  : "bg-elevated text-muted border-border hover:text-foreground",
              )}
            >
              {f === "all" ? t("filterAll") : t(`decision.${f}`)}
              <span className="ml-1 opacity-70">{counts[f] ?? 0}</span>
            </button>
          ))}
        </div>
        <div className="relative sm:ml-auto sm:w-56">
          <Search size={14} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-muted pointer-events-none" />
          <Input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder={t("searchTool")}
            className="pl-8 h-8"
          />
        </div>
      </div>

      {filtered.length === 0 ? (
        <EmptyState icon={History} title={history.length === 0 ? t("noHistory") : t("noMatch")} />
      ) : (
        <div className="space-y-2">
          {filtered.map((item) => {
            const isExpanded = expandedId === item.id;
            const Icon = DECISION_ICON[item.outcome] ?? Clock;
            const absolute = new Date(item.ts_ns / 1e6).toLocaleString();

            return (
              <div
                key={item.id}
                className="rounded-lg border border-border bg-card overflow-hidden transition-shadow hover:shadow-sm animate-rise"
              >
                <button
                  type="button"
                  aria-expanded={isExpanded}
                  className="flex w-full text-left flex-wrap sm:flex-nowrap items-center gap-3 p-3 cursor-pointer"
                  onClick={() => setExpandedId(isExpanded ? null : item.id)}
                >
                  <Icon size={18} className={cn("shrink-0", DECISION_ICON_COLOR[item.outcome])} />
                  <div className="flex-1 min-w-0">
                    <div className="font-mono text-sm text-heading truncate">{item.tool_name}</div>
                    <div className="text-xs text-muted mt-0.5 truncate">
                      {item.channel_id || t("system")} · {item.user_id || item.chat_id || "—"}
                    </div>
                  </div>
                  <Badge variant={DECISION_VARIANT[item.outcome] ?? "neutral"}>
                    {t(`decision.${item.outcome}`, { defaultValue: item.outcome })}
                  </Badge>
                  <span className="text-xs text-muted shrink-0" title={absolute}>
                    {relativeTime(item.ts_ns / 1e9, t)}
                  </span>
                  {isExpanded ? <ChevronUp size={14} className="shrink-0 text-muted" /> : <ChevronDown size={14} className="shrink-0 text-muted" />}
                </button>

                {isExpanded && (
                  <div className="px-4 pb-4 pt-3 grid grid-cols-1 sm:grid-cols-2 gap-3 border-t border-border">
                    <div>
                      <div className="text-xs font-medium text-muted mb-1">{t("decidedBy")}</div>
                      <div className="text-sm text-foreground">{item.decided_by || t("system")}</div>
                    </div>
                    <div>
                      <div className="text-xs font-medium text-muted mb-1">{t("riskLevel")}</div>
                      <div className="text-sm text-foreground">{t(`risk.${item.risk_level.toLowerCase()}`)}</div>
                    </div>
                    {item.matched_rule && (
                      <div className="sm:col-span-2">
                        <div className="text-xs font-medium text-muted mb-1">{t("matchedRule")}</div>
                        <div className="font-mono text-xs text-foreground bg-elevated border border-border rounded-md px-3 py-2">
                          {item.matched_rule}
                        </div>
                      </div>
                    )}
                    {item.args_json && (
                      <div className="sm:col-span-2">
                        <div className="text-xs font-medium text-muted mb-1">{t("arguments")}</div>
                        <pre className="text-xs whitespace-pre-wrap break-all rounded-md bg-elevated p-3">{item.args_json}</pre>
                      </div>
                    )}
                    {item.reason && (
                      <div className="sm:col-span-2">
                        <div className="text-xs font-medium text-muted mb-1">{t("decisionReason")}</div>
                        <div className="text-sm text-foreground">{item.reason}</div>
                      </div>
                    )}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
      <div className="flex justify-between items-center gap-3 text-sm text-muted">
        <Button disabled={offset === 0 || isLoading} onClick={() => setOffset(Math.max(0, offset - 50))}>{t("previous")}</Button>
        <span>{t("page", { number: Math.floor(offset / 50) + 1 })}</span>
        <Button disabled={history.length < 50 || isLoading} onClick={() => setOffset(offset + 50)}>{t("next")}</Button>
      </div>
    </div>
  );
}
