import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useQuery } from "@tanstack/react-query";
import { shareApi } from "@/lib/api";
import { Card } from "@/components/common/Card";
import { RefreshCw, Search, ScrollText } from "lucide-react";

function formatTime(ms: number): string {
  if (!ms) return "-";
  return new Date(ms).toLocaleString();
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

export function LogsPanel() {
  const { t } = useTranslation("share");
  const [token, setToken] = useState("");
  const [page, setPage] = useState(1);
  const pageSize = 50;

  const { data, isLoading, refetch } = useQuery({
    queryKey: ["shareLogs", token, page],
    queryFn: () =>
      shareApi.getLogs({ token: token || undefined, page, page_size: pageSize }).then((r) => r.data),
    refetchInterval: 10000,
  });

  const totalPages = data ? Math.ceil(data.total / pageSize) : 0;

  return (
    <div className="space-y-4">
      <Card
        title={t("tabs.logs")}
        subtitle={data ? `${t("stats.total")}: ${data.total}` : undefined}
        actions={
          <button
            onClick={() => refetch()}
            className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-md border border-border bg-elevated text-muted hover:bg-hover transition-all"
          >
            <RefreshCw size={14} /> {t("actions.refresh")}
          </button>
        }
      >
        {/* 筛选栏 */}
        <div className="flex flex-wrap items-center gap-3 mb-4">
          <div className="flex items-center gap-2 flex-1 min-w-[200px]">
            <Search size={16} className="text-muted flex-shrink-0" />
            <input
              type="text"
              placeholder={t("fields.token") + "..."}
              value={token}
              onChange={(e) => {
                setToken(e.target.value);
                setPage(1);
              }}
              className="flex-1 px-3 py-1.5 text-sm rounded-md border border-border bg-elevated text-foreground placeholder:text-muted focus:outline-none focus:ring-1 focus:ring-accent"
            />
          </div>
        </div>

        {/* 日志列表 */}
        {isLoading ? (
          <div className="text-sm text-muted text-center py-8">{t("common:loading")}</div>
        ) : !data || data.items.length === 0 ? (
          <div className="text-center py-12">
            <ScrollText size={48} className="mx-auto mb-4 text-muted opacity-50" />
            <p className="text-sm text-muted">{t("empty.logs")}</p>
          </div>
        ) : (
          <>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-border text-left text-muted text-xs uppercase tracking-wider">
                    <th className="pb-2 pr-4">{t("fields.fileName")}</th>
                    <th className="pb-2 pr-4">{t("fields.ip")}</th>
                    <th className="pb-2 pr-4">{t("fields.downloadedAt")}</th>
                    <th className="pb-2 pr-4">{t("fields.fileSize")}</th>
                    <th className="pb-2">{t("fields.userAgent")}</th>
                  </tr>
                </thead>
                <tbody>
                  {data.items.map((log) => (
                    <tr
                      key={log.id}
                      className="border-b border-border hover:bg-hover transition-colors"
                    >
                      <td className="py-3 pr-4">
                        <div className="font-medium text-heading">{log.file_name}</div>
                        <div className="text-xs text-muted font-mono">{log.token.slice(0, 12)}...</div>
                      </td>
                      <td className="py-3 pr-4 text-muted">{log.ip || "-"}</td>
                      <td className="py-3 pr-4 text-muted">{formatTime(log.downloaded_at)}</td>
                      <td className="py-3 pr-4 text-muted">{formatSize(log.file_size)}</td>
                      <td className="py-3 text-muted max-w-[200px] truncate" title={log.user_agent}>
                        {log.user_agent || "-"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            {/* 分页 */}
            {totalPages > 1 && (
              <div className="flex items-center justify-between mt-4 pt-4 border-t border-border">
                <span className="text-xs text-muted">
                  {page} / {totalPages}
                </span>
                <div className="flex gap-2">
                  <button
                    onClick={() => setPage((p) => Math.max(1, p - 1))}
                    disabled={page <= 1}
                    className="px-3 py-1.5 text-xs font-medium rounded-md border border-border bg-elevated text-muted hover:bg-hover transition-all disabled:opacity-50 disabled:cursor-not-allowed"
                  >
                    ←
                  </button>
                  <button
                    onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
                    disabled={page >= totalPages}
                    className="px-3 py-1.5 text-xs font-medium rounded-md border border-border bg-elevated text-muted hover:bg-hover transition-all disabled:opacity-50 disabled:cursor-not-allowed"
                  >
                    →
                  </button>
                </div>
              </div>
            )}
          </>
        )}
      </Card>
    </div>
  );
}
