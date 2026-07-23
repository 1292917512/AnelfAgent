import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { approvalsApi, statusApi, mcpApi, adaptersApi } from "@/lib/api";
import type { LogEntry, MCPServer, AdapterInfo } from "@/lib/types";
import { Card } from "@/components/common/Card";
import { Badge } from "@/components/ui/Badge";
import { cn } from "@/lib/utils";
import {
  ShieldAlert,
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

function logTime(e: LogEntry): number {
  const t = Date.parse(e.time);
  return Number.isNaN(t) ? 0 : t;
}

/** 总览页「需要注意」区块：聚合待批准、异常日志、MCP/通道故障等需要人工关注的问题 */
export function AttentionPanel() {
  const { t } = useTranslation(["dashboard", "approvals", "common"]);

  const { data: status } = useQuery({
    queryKey: ["status"],
    queryFn: () => statusApi.get().then((r) => r.data),
    refetchInterval: 3000,
  });
  const { data: pendingData } = useQuery({
    queryKey: ["approvals", "pending"],
    queryFn: () => approvalsApi.pending().then((r) => r.data),
    refetchInterval: 3000,
  });
  const { data: errorLogs } = useQuery({
    queryKey: ["logs", "ERROR"],
    queryFn: () => statusApi.logs("ERROR", undefined, undefined, 5).then((r) => r.data),
    refetchInterval: 10000,
  });
  const { data: warnLogs } = useQuery({
    queryKey: ["logs", "WARNING"],
    queryFn: () => statusApi.logs("WARNING", undefined, undefined, 5).then((r) => r.data),
    refetchInterval: 10000,
  });
  const { data: mcpServers } = useQuery({
    queryKey: ["mcp"],
    queryFn: () => mcpApi.list().then((r) => r.data),
    refetchInterval: 5000,
  });
  const { data: adapters } = useQuery({
    queryKey: ["adapters"],
    queryFn: () => adaptersApi.list().then((r) => r.data),
    refetchInterval: 10000,
  });

  const items: AttentionItem[] = [];

  // Agent 未就绪
  if (status && !status.ready) {
    items.push({
      key: "not-ready",
      severity: "danger",
      icon: AlertTriangle,
      title: t("attention.notReady"),
    });
  }

  // 待处理批准请求
  const pending = (pendingData?.pending ?? []) as { request_id: string; tool_name: string; expires_at: number }[];
  if (pending.length > 0) {
    const nearest = Math.min(...pending.map((p) => p.expires_at));
    const seconds = Math.max(0, Math.floor(nearest - Date.now() / 1000));
    items.push({
      key: "pending-approvals",
      severity: seconds < 30 ? "danger" : "warn",
      icon: ShieldAlert,
      title: t("attention.pendingApprovals", { count: pending.length }),
      desc: t("attention.nearestExpiry", { seconds }),
      to: "/approvals",
    });
  }

  // MCP 服务器启用但未连接
  (mcpServers ?? []).forEach((s: MCPServer) => {
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
  (adapters?.adapters ?? []).forEach((a: AdapterInfo) => {
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

  // 最近的 ERROR / WARNING 日志（相同消息去重，避免重复告警刷屏）
  const seen = new Set<string>();
  const recentLogs = [...(errorLogs?.logs ?? []), ...(warnLogs?.logs ?? [])]
    .sort((a, b) => logTime(b) - logTime(a))
    .filter((log) => {
      if (seen.has(log.message)) return false;
      seen.add(log.message);
      return true;
    })
    .slice(0, 5);
  recentLogs.forEach((log, i) => {
    items.push({
      key: `log-${i}`,
      severity: log.level === "ERROR" || log.level === "CRITICAL" ? "danger" : "warn",
      icon: ScrollText,
      title: log.message.length > 120 ? `${log.message.slice(0, 120)}…` : log.message,
      desc: [log.time, log.tag].filter(Boolean).join(" · "),
      to: "/dashboard?tab=logs",
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
        ) : (
          <Badge variant="ok">{t("attention.okShort")}</Badge>
        )
      }
    >
      {items.length === 0 ? (
        <div className="flex items-center gap-3 py-2 text-sm text-ok">
          <CheckCircle2 size={18} />
          <span>{t("attention.allClear")}</span>
        </div>
      ) : (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-2">
          {items.map((item) => {
            const inner = (
              <>
                <item.icon size={16} className={cn("shrink-0 mt-0.5", SEVERITY_ICON[item.severity])} />
                <div className="flex-1 min-w-0">
                  <div className="text-sm text-foreground truncate">{item.title}</div>
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
    </Card>
  );
}
