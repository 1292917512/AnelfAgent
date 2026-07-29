import { useTranslation } from "react-i18next";
import { useQuery } from "@tanstack/react-query";
import { shareApi } from "@/lib/api";
import { Card } from "@/components/common/Card";
import { StatCard } from "@/components/common/StatCard";
import { Link2, Download } from "lucide-react";

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function formatTime(ms: number): string {
  if (!ms) return "-";
  const d = new Date(ms);
  const now = new Date();
  const diff = now.getTime() - d.getTime();
  if (diff < 60000) return "刚刚";
  if (diff < 3600000) return `${Math.floor(diff / 60000)} 分钟前`;
  if (diff < 86400000) return `${Math.floor(diff / 3600000)} 小时前`;
  return d.toLocaleDateString();
}

export default function SharePanel() {
  const { t } = useTranslation("share");

  const { data: stats } = useQuery({
    queryKey: ["shareStats"],
    queryFn: () => shareApi.stats().then((r) => r.data),
    refetchInterval: 5000,
  });

  const { data: links } = useQuery({
    queryKey: ["shareLinks", "active"],
    queryFn: () => shareApi.list({ status: "active", page_size: 5 }).then((r) => r.data),
    refetchInterval: 5000,
  });

  const { data: logs } = useQuery({
    queryKey: ["shareLogs", "recent"],
    queryFn: () => shareApi.getLogs({ page_size: 10 }).then((r) => r.data),
    refetchInterval: 5000,
  });

  const totalSize = links?.items?.reduce((sum: number, l: { file_size: number }) => sum + l.file_size, 0) ?? 0;

  return (
    <div className="space-y-4">
      {/* 实时监控卡片 */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <StatCard
          label={t("monitor.activeLinks")}
          value={String(stats?.active ?? 0)}
          variant="ok"
        />
        <StatCard
          label={t("stats.totalDownloads")}
          value={String(stats?.total_downloads ?? 0)}
        />
        <StatCard
          label={t("monitor.storage")}
          value={formatSize(totalSize)}
        />
        <StatCard
          label={t("stats.expired")}
          value={String(stats?.expired ?? 0)}
          variant="warn"
        />
      </div>

      {/* 活跃链接 */}
      <Card title={t("monitor.activeLinks")} subtitle={`${links?.total ?? 0} ${t("status.active")}`}>
        {links?.items && links.items.length > 0 ? (
          <div className="space-y-2">
            {links.items.map((link) => (
              <div
                key={link.token}
                className="flex items-center justify-between p-3 rounded-md bg-elevated border border-border"
              >
                <div className="flex items-center gap-3 min-w-0">
                  <Link2 size={16} className="flex-shrink-0 text-accent" />
                  <div className="min-w-0">
                    <div className="text-sm font-medium text-heading truncate">
                      {link.file_name}
                    </div>
                    <div className="text-xs text-muted truncate">{link.file_path}</div>
                  </div>
                </div>
                <div className="flex items-center gap-4 text-sm">
                  <span className="text-muted">{formatSize(link.file_size)}</span>
                  <span className="text-muted">{link.download_count} ↓</span>
                  <span className="text-xs text-muted">
                    {link.expires_at ? formatTime(link.expires_at) : t("expires.never")}
                  </span>
                </div>
              </div>
            ))}
          </div>
        ) : (
          <p className="text-sm text-muted text-center py-4">{t("empty.title")}</p>
        )}
      </Card>

      {/* 最近下载 */}
      <Card title={t("monitor.recentDownloads")} subtitle={`${logs?.total ?? 0} ${t("tabs.logs")}`}>
        {logs?.items && logs.items.length > 0 ? (
          <div className="space-y-2">
            {logs.items.map((log) => (
              <div
                key={log.id}
                className="flex items-center justify-between p-3 rounded-md bg-elevated border border-border"
              >
                <div className="flex items-center gap-3 min-w-0">
                  <Download size={16} className="flex-shrink-0 text-ok" />
                  <div className="min-w-0">
                    <div className="text-sm font-medium text-heading truncate">
                      {log.file_name}
                    </div>
                    <div className="text-xs text-muted truncate">{log.ip || "-"}</div>
                  </div>
                </div>
                <div className="text-xs text-muted">
                  {formatTime(log.downloaded_at)}
                </div>
              </div>
            ))}
          </div>
        ) : (
          <p className="text-sm text-muted text-center py-4">{t("empty.logs")}</p>
        )}
      </Card>
    </div>
  );
}
