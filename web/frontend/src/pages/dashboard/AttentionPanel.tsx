import { useQuery } from "@tanstack/react-query";

import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { statusApi, adaptersApi } from "@/lib/api";
import { mcpServersQuery } from "@/lib/mcp-queries";
import { runtimeStatusQuery } from "@/lib/runtime-queries";
import { groupAttentionLogs } from "./log-buffer";
import { Card } from "@/components/common/Card";
import { QueryError } from "@/components/common/AsyncState";
import { Badge } from "@/components/ui/Badge";
import { cn } from "@/lib/utils";
import {
  Plug,
  Radio,
  ScrollText,
  CheckCircle2,
  ChevronRight,
  AlertTriangle,
  type LucideIcon,
} from "lucide-react";

type Severity = "danger" | "warn";

type AttentionItem = {
  key: string;
  severity: Severity;
  icon: LucideIcon;
  title: string;
  desc?: string;
  to?: string;
};

const SEVERITY_STYLE: Record<Severity, string> = {
  danger: "border-danger/40 bg-danger-subtle",
  warn: "border-warn/40 bg-warn-subtle",
};

const SEVERITY_ICON: Record<Severity, string> = {
  danger: "text-danger",
  warn: "text-warn",
};

/** 总览页「需要注意」区块：聚合异常日志、MCP/通道故障等需要人工关注的问题 */
export function AttentionPanel() {
  const { t } = useTranslation(["dashboard", "common"]);

  const status = useQuery(runtimeStatusQuery());
  const errorLogs = useQuery({
    queryKey: ["logs", "ERROR"],
    queryFn: async ({ signal }) => {
      const results = await Promise.all(["ERROR", "CRITICAL"].map((level) => statusApi.logs(level, undefined, undefined, 5, signal)));
      return { logs: results.flatMap((result) => result.data.logs) };
    },
    refetchInterval: 10000, throwOnError: false,
  });
  const warnLogs = useQuery({
    queryKey: ["logs", "WARNING"],
    queryFn: ({ signal }) => statusApi.logs("WARNING", undefined, undefined, 5, signal).then((r) => r.data),
    refetchInterval: 10000, throwOnError: false,
  });
  const mcpServers = useQuery({ ...mcpServersQuery(), refetchInterval: 5000 });
  const adapters = useQuery({
    queryKey: ["adapters"],
    queryFn: ({ signal }) => adaptersApi.list(signal).then((r) => r.data),
    refetchInterval: 10000, throwOnError: false,
  });

  const sources = [status, errorLogs, warnLogs, mcpServers, adapters];
  const failed = sources.filter((query) => query.isError);
  const firstFailure = failed[0];
  const pending = sources.some((query) => query.isPending);
  const complete = !pending && !failed.length;
  const items: AttentionItem[] = [];

  // Agent 未就绪
  if (status.isSuccess && !status.data.ready) {
    items.push({
      key: "not-ready",
      severity: "danger",
      icon: AlertTriangle,
      title: t("attention.notReady"),
    });
  }

  // MCP 服务器启用但未连接
  if (mcpServers.isSuccess) mcpServers.data.forEach((s) => {
    if (s.enabled && !s.connected) {
      items.push({
        key: `mcp-${s.name}`,
        severity: "danger",
        icon: Plug,
        title: t("attention.mcpDown", { name: s.name }),
        desc: s.last_error || undefined,
        to: "/mcp",
      });
    }
  });

  // 通道适配器异常
  if (adapters.data?.ready === false && status.data?.ready) {
    items.push({ key: "adapters-not-ready", severity: "warn", icon: Radio, title: t("attention.channelsNotReady"), to: "/channels" });
  }
  if (adapters.isSuccess) adapters.data.adapters.forEach((a) => {
    if (a.status === "error") {
      items.push({
        key: `adapter-${a.key}`,
        severity: "danger",
        icon: Radio,
        title: t("attention.channelError", { name: a.name }),
        desc: a.detail || a.status_display,
        to: "/channels",
      });
    }
  });

  const recentLogs = groupAttentionLogs([...(errorLogs.isSuccess ? errorLogs.data.logs : []), ...(warnLogs.isSuccess ? warnLogs.data.logs : [])]).slice(0, 5);
  recentLogs.forEach(({ log, count, danger }) => {
    items.push({
      key: `log-${log.tag}-${log.message}`,
      severity: danger ? "danger" : "warn",
      icon: ScrollText,
      title: log.message.length > 120 ? `${log.message.slice(0, 120)}…` : log.message,
      desc: [log.time, log.tag, count > 1 ? t("attention.repeated", { count }) : ""].filter(Boolean).join(" · "),
      to: `/dashboard?tab=logs&jump=${encodeURIComponent(log.message)}`,
    });
  });

  return (
    <Card
      title={t("attention.title")}
      actions={
        items.length > 0 ? (
          <Badge variant={items.some((i) => i.severity === "danger") ? "danger" : "warn"}>
            {items.length}
          </Badge>
        ) : complete && (
          <Badge variant="ok">{t("attention.okShort")}</Badge>
        )
      }
    >
      <div className="min-w-0 space-y-2">
        {firstFailure && <QueryError compact title={t("attention.partial")} error={firstFailure.error} retry={() => { failed.forEach((query) => { void query.refetch(); }); }} />}
        {pending && <p role="status" className="text-sm text-muted">{t("common:loading")}</p>}
        {items.length === 0 && complete ? (
          <div className="flex items-center gap-3 py-2 text-sm text-ok">
            <CheckCircle2 size={18} />
            <span>{t("attention.allClear")}</span>
          </div>
        ) : (
          <div className="overview-attention">
            {items.map((item) => {
              const inner = (
                <>
                  <item.icon size={16} className={cn("shrink-0 mt-0.5", SEVERITY_ICON[item.severity])} />
                  <div className="flex-1 min-w-0">
                    <div className="text-sm text-foreground line-clamp-2 break-words">{item.title}</div>
                    {item.desc && <div className="text-xs text-muted truncate mt-0.5">{item.desc}</div>}
                  </div>
                  {item.to && <ChevronRight size={14} className="shrink-0 text-muted mt-1" />}
                </>
              );
              const cls = cn(
                "flex items-start gap-2.5 px-3 py-2.5 rounded-md border transition-colors",
                SEVERITY_STYLE[item.severity],
                item.to && "hover:border-border-strong cursor-pointer",
              );
              return item.to ? (
                <Link key={item.key} to={item.to} className={cls}>
                  {inner}
                </Link>
              ) : (
                <div key={item.key} className={cls}>
                  {inner}
                </div>
              );
            })}
          </div>
        )}
      </div>
    </Card>
  );
}
