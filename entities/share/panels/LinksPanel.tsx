import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { shareApi } from "@/lib/api";
import { Card } from "@/components/common/Card";
import { Copy, Download, ExternalLink, FileText, Globe, Image as ImageIcon, Link2, Music, RefreshCw, Search, Trash2, Video } from "lucide-react";
import { toast } from "@/stores/toast-store";
import type { ShareLink } from "@/lib/types";

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function formatTime(ms: number): string {
  if (!ms) return "-";
  return new Date(ms).toLocaleString();
}

function statusVariant(status: string): "ok" | "warn" | "danger" | "default" {
  switch (status) {
    case "active": return "ok";
    case "expired": return "warn";
    case "revoked": return "danger";
    default: return "default";
  }
}

/** 分享类型徽标（图标 + 文案） */
function TypeBadge({ link }: { link: ShareLink }) {
  const { t } = useTranslation("share");
  let icon = <FileText size={12} />;
  let label = t("types.file.name");
  if (link.share_type === "link") {
    icon = <Globe size={12} />;
    label = t("types.link.name");
  } else if (link.share_type === "media") {
    label = t("types.media.name");
    icon = link.media_kind === "image" ? <ImageIcon size={12} />
      : link.media_kind === "video" ? <Video size={12} />
      : link.media_kind === "audio" ? <Music size={12} />
      : <FileText size={12} />;
  }
  return (
    <span className="inline-flex items-center gap-1 px-1.5 py-0.5 text-[11px] rounded bg-elevated border border-border text-muted whitespace-nowrap">
      {icon} {label}
    </span>
  );
}

export function LinksPanel() {
  const { t } = useTranslation("share");
  const queryClient = useQueryClient();
  const [status, setStatus] = useState<string>("active");
  const [query, setQuery] = useState("");
  const [page, setPage] = useState(1);
  const pageSize = 20;

  const { data, isLoading, refetch } = useQuery({
    queryKey: ["shareLinks", status, query, page],
    queryFn: () =>
      shareApi.list({ status, query, page, page_size: pageSize }).then((r) => r.data),
  });

  const revokeMutation = useMutation({
    mutationFn: (token: string) => shareApi.revoke(token),
    onSuccess: () => {
      toast.success(t("messages.revokeSuccess"));
      queryClient.invalidateQueries({ queryKey: ["shareLinks"] });
      queryClient.invalidateQueries({ queryKey: ["shareStats"] });
    },
    onError: () => toast.error(t("messages.revokeFailed")),
  });

  const copyUrl = (link: ShareLink) => {
    const url = link.url || `${window.location.origin}/api/entity/share/v/${link.token}`;
    navigator.clipboard.writeText(url).then(
      () => toast.success(t("messages.copySuccess")),
      () => toast.error(t("messages.copyFailed")),
    );
  };

  const totalPages = data ? Math.ceil(data.total / pageSize) : 0;

  return (
    <div className="space-y-4">
      <Card
        title={t("tabs.links")}
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
              placeholder={t("actions.search") + "..."}
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              className="flex-1 px-3 py-1.5 text-sm rounded-md border border-border bg-elevated text-foreground placeholder:text-muted focus:outline-none focus:ring-1 focus:ring-accent"
            />
          </div>
          <select
            value={status}
            onChange={(e) => {
              setStatus(e.target.value);
              setPage(1);
            }}
            className="px-3 py-1.5 text-sm rounded-md border border-border bg-elevated text-foreground focus:outline-none focus:ring-1 focus:ring-accent"
          >
            <option value="active">{t("status.active")}</option>
            <option value="expired">{t("status.expired")}</option>
            <option value="revoked">{t("status.revoked")}</option>
            <option value="all">{t("stats.total")}</option>
          </select>
        </div>

        {/* 链接列表 */}
        {isLoading ? (
          <div className="text-sm text-muted text-center py-8">{t("common:loading")}</div>
        ) : !data || data.items.length === 0 ? (
          <div className="text-center py-12">
            <Link2 size={48} className="mx-auto mb-4 text-muted opacity-50" />
            <p className="text-sm text-muted">{t("empty.title")}</p>
            <p className="text-xs text-muted mt-1">{t("empty.hint")}</p>
          </div>
        ) : (
          <>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-border text-left text-muted text-xs uppercase tracking-wider">
                    <th className="pb-2 pr-4">{t("fields.fileName")}</th>
                    <th className="pb-2 pr-4">{t("fields.fileSize")}</th>
                    <th className="pb-2 pr-4">{t("fields.expiresAt")}</th>
                    <th className="pb-2 pr-4">{t("fields.downloads")}</th>
                    <th className="pb-2 pr-4">{t("fields.status")}</th>
                    <th className="pb-2">{t("fields.url")}</th>
                  </tr>
                </thead>
                <tbody>
                  {data.items.map((link: ShareLink) => (
                    <tr
                      key={link.token}
                      className="border-b border-border hover:bg-hover transition-colors"
                    >
                      <td className="py-3 pr-4">
                        <div className="font-medium text-heading">{link.file_name}</div>
                        <div className="flex items-center gap-1.5 mt-1">
                          <TypeBadge link={link} />
                        </div>
                        <div className="text-xs text-muted truncate max-w-[200px] mt-0.5">
                          {link.description || (link.share_type === "link" ? link.target_url : link.file_path)}
                        </div>
                      </td>
                      <td className="py-3 pr-4 text-muted">{formatSize(link.file_size)}</td>
                      <td className="py-3 pr-4 text-muted">
                        {link.expires_at ? formatTime(link.expires_at) : t("expires.never")}
                      </td>
                      <td className="py-3 pr-4 text-muted">{link.download_count}</td>
                      <td className="py-3 pr-4">
                        <span
                          className={`inline-flex px-2 py-0.5 text-xs font-medium rounded-full ${
                            statusVariant(link.status) === "ok"
                              ? "bg-ok-subtle text-ok"
                              : statusVariant(link.status) === "warn"
                                ? "bg-warn-subtle text-warn"
                                : "bg-danger-subtle text-danger"
                          }`}
                        >
                          {t(`status.${link.status}`)}
                        </span>
                      </td>
                      <td className="py-3">
                        <div className="flex items-center gap-1">
                          <button
                            onClick={() => copyUrl(link)}
                            className="p-1.5 rounded-md text-muted hover:text-foreground hover:bg-hover transition-all"
                            title={t("actions.copy")}
                          >
                            <Copy size={14} />
                          </button>
                          {link.status === "active" && (
                            <>
                              {link.share_type === "file" ? (
                                <a
                                  href={link.download_url || link.url || `/api/entity/share/d/${link.token}`}
                                  target="_blank"
                                  rel="noreferrer"
                                  className="p-1.5 rounded-md text-muted hover:text-foreground hover:bg-hover transition-all"
                                  title={t("actions.download")}
                                >
                                  <Download size={14} />
                                </a>
                              ) : (
                                <a
                                  href={link.url || `/api/entity/share/v/${link.token}`}
                                  target="_blank"
                                  rel="noreferrer"
                                  className="p-1.5 rounded-md text-muted hover:text-foreground hover:bg-hover transition-all"
                                  title={t("actions.openPreview")}
                                >
                                  <ExternalLink size={14} />
                                </a>
                              )}
                              <button
                                onClick={() => {
                                  if (window.confirm(t("messages.confirmRevoke"))) {
                                    revokeMutation.mutate(link.token);
                                  }
                                }}
                                className="p-1.5 rounded-md text-danger hover:bg-danger-subtle transition-all"
                                title={t("actions.revoke")}
                              >
                                <Trash2 size={14} />
                              </button>
                            </>
                          )}
                        </div>
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
