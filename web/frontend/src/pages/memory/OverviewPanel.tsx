import { useTranslation } from "react-i18next";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { memoryApi } from "@/lib/api";
import { Card } from "@/components/common/Card";
import { StatCard } from "@/components/common/StatCard";
import { Trash2, RefreshCw, AlertTriangle } from "lucide-react";

export function OverviewPanel() {
  const { t } = useTranslation("memory");
  const queryClient = useQueryClient();
  const { data: health } = useQuery({
    queryKey: ["memoryHealth"],
    queryFn: () => memoryApi.health().then((r) => r.data),
    refetchInterval: 10000,
  });
  const { data: indexStatus } = useQuery({ queryKey: ["indexStatus"], queryFn: () => memoryApi.index.status().then((r) => r.data) });
  const resyncMutation = useMutation({ mutationFn: (force: boolean) => memoryApi.index.resync(force), onSuccess: () => queryClient.invalidateQueries({ queryKey: ["indexStatus"] }) });
  const cleanCacheMutation = useMutation({ mutationFn: () => memoryApi.index.cleanCache(), onSuccess: () => queryClient.invalidateQueries({ queryKey: ["indexStatus"] }) });

  const typeCounts = (health?.type_counts || {}) as Record<string, number>;
  const warnings = (health?.warnings || []) as string[];
  const warnThreshold = health?.warn_threshold || 200;

  return (
    <div className="space-y-4">
      {(!health || health.error) ? (
        <Card title={t("healthTitle")}><p className="text-sm text-[var(--muted)]">{health?.error || t("common:loading")}</p></Card>
      ) : (
        <Card title={t("healthSubtitle")} subtitle={t("totalMemories", { count: health.total_memories || 0 })}>
          <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-5 gap-3 mb-4">
            {Object.entries(typeCounts).map(([type, count]) => (
              <StatCard key={type} label={type} value={String(count)}
                variant={count >= warnThreshold ? "danger" : count >= warnThreshold * 0.7 ? "warn" : "default"} />
            ))}
          </div>
          {warnings.length > 0 && (
            <div className="space-y-2 mb-4">
              {warnings.map((w) => (
                <div key={w} className="flex items-start gap-2 p-3 rounded-[var(--radius-md)] bg-[var(--warn-subtle)] border border-[var(--warn)] text-sm">
                  <AlertTriangle size={16} className="flex-shrink-0 mt-0.5 text-[var(--warn)]" />
                  <span className="text-[var(--text)]">{w}</span>
                </div>
              ))}
            </div>
          )}
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            <StatCard label={t("embedding")} value={health.embedding_available ? `${t("common:available")} (${health.embedding_dims || "?"}维)` : t("common:unavailable")}
              variant={health.embedding_available ? "ok" : "default"} />
            <StatCard label={t("ftsSearch")} value={health.fts_available ? t("common:available") : t("common:unavailable")}
              variant={health.fts_available ? "ok" : "default"} />
            <StatCard label={t("fileIndex")} value={t("filesChunks", { files: health.files || 0, chunks: health.chunks || 0 })} />
            <StatCard label={t("cache")} value={t("cacheEntries", { count: health.embedding_cache || 0 })} />
          </div>
        </Card>
      )}

      <Card title={t("fileIndexTitle")} subtitle={t("fileIndexSubtitle")} actions={
        <div className="flex gap-2">
          <button onClick={() => cleanCacheMutation.mutate()}
            className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-[var(--radius-md)] border border-[var(--border)] bg-[var(--bg-elevated)] text-[var(--muted)] hover:bg-[var(--bg-hover)] transition-all">
            <Trash2 size={14} /> {t("cleanCache")}
          </button>
          <button onClick={() => resyncMutation.mutate(false)}
            className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-[var(--radius-md)] border border-[var(--border)] bg-[var(--bg-elevated)] text-[var(--muted)] hover:bg-[var(--bg-hover)] transition-all">
            <RefreshCw size={14} /> {t("incrementalSync")}
          </button>
          <button onClick={() => resyncMutation.mutate(true)}
            className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-[var(--radius-md)] border border-[var(--border)] bg-[var(--danger-subtle)] text-[var(--danger)] hover:bg-[rgba(239,68,68,0.15)] transition-all">
            <RefreshCw size={14} /> {t("fullRebuild")}
          </button>
        </div>
      }>
        {indexStatus ? (
          <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-3">
            {Object.entries(indexStatus as Record<string, unknown>).map(([k, v]) => (
              <StatCard key={k} label={t(`indexLabels.${k}`, { defaultValue: k })} value={typeof v === "boolean" ? (v ? t("common:available") : t("common:unavailable")) : String(v)}
                variant={typeof v === "boolean" ? (v ? "ok" : "default") : undefined} />
            ))}
          </div>
        ) : (<p className="text-sm text-[var(--muted)]">{t("common:loading")}</p>)}
      </Card>
    </div>
  );
}
