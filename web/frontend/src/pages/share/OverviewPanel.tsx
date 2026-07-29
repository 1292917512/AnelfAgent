import { useTranslation } from "react-i18next";
import { useQuery } from "@tanstack/react-query";
import { shareApi } from "@/lib/api";
import { Card } from "@/components/common/Card";
import { StatCard } from "@/components/common/StatCard";
import { FileText, TrendingUp } from "lucide-react";

export function OverviewPanel() {
  const { t } = useTranslation("share");
  const { data: stats } = useQuery({
    queryKey: ["shareStats"],
    queryFn: () => shareApi.stats().then((r) => r.data),
    refetchInterval: 15000,
  });

  return (
    <div className="space-y-4">
      <Card title={t("tabs.overview")} subtitle={t("stats.total") + `: ${stats?.total ?? 0}`}>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          <StatCard
            label={t("stats.active")}
            value={String(stats?.active ?? 0)}
            variant="ok"
          />
          <StatCard
            label={t("stats.expired")}
            value={String(stats?.expired ?? 0)}
            variant="warn"
          />
          <StatCard
            label={t("stats.revoked")}
            value={String(stats?.revoked ?? 0)}
            variant="danger"
          />
          <StatCard
            label={t("stats.totalDownloads")}
            value={String(stats?.total_downloads ?? 0)}
          />
        </div>
      </Card>

      {stats?.top_files && stats.top_files.length > 0 && (
        <Card title={t("topFiles")} subtitle={t("trend")}>
          <div className="space-y-2">
            {stats.top_files.map((file: { file_path: string; file_name: string; count: number }) => (
              <div
                key={file.file_path}
                className="flex items-center justify-between p-3 rounded-md bg-elevated border border-border hover:bg-hover transition-all"
              >
                <div className="flex items-center gap-3 min-w-0">
                  <FileText size={16} className="flex-shrink-0 text-muted" />
                  <div className="min-w-0">
                    <div className="text-sm font-medium text-heading truncate">
                      {file.file_name}
                    </div>
                    <div className="text-xs text-muted truncate">{file.file_path}</div>
                  </div>
                </div>
                <div className="flex items-center gap-1.5 text-sm font-semibold text-accent">
                  <TrendingUp size={14} />
                  {file.count}
                </div>
              </div>
            ))}
          </div>
        </Card>
      )}
    </div>
  );
}
