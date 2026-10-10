import { useState } from "react";
import { useTranslation } from "react-i18next";
import { skipToken, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Download, Trash2, ChevronRight } from "lucide-react";
import { contextApi } from "@/lib/api";
import { SnapshotDetail } from "@/components/context/SnapshotDetail";
import { cn } from "@/lib/utils";
import type { SnapshotListItem } from "@/lib/types";
import { downloadJson } from "./downloadJson";
import { PageSkeleton, QueryError } from "@/components/common/AsyncState";

/** 缓存命中率徽标：≥70% 绿 / ≥30% 黄 / 其余灰；
 *  合法断裂窗口（折叠/压缩刚重写前缀）= 首轮低命中属预期，单独标识；
 *  前缀字节稳定但命中低 = 供应商侧缓存波动（非内容断裂），单独标识 */
function CacheHitBadge({ rate, prefixStable, legalBreak }: { rate?: number | null; prefixStable?: boolean | null; legalBreak?: string | null }) {
  const { t } = useTranslation("context");
  if (rate == null) return null;
  const pct = Math.round(rate * 100);
  if (pct < 70 && legalBreak) {
    return (
      <span
        className="px-1.5 py-px rounded text-[11px] font-medium bg-accent-subtle text-accent"
        title={t(`history.legalBreakDesc.${legalBreak}`, { defaultValue: legalBreak })}
      >
        {pct}% · {t(`history.legalBreak.${legalBreak}`, { defaultValue: legalBreak })}
      </span>
    );
  }
  if (pct < 70 && prefixStable === true) {
    return (
      <span
        className="px-1.5 py-px rounded text-[9px] font-medium bg-sky-500/15 text-info"
        title={t("history.jitterDesc")}
      >
        {pct}% · {t("history.jitter")}
      </span>
    );
  }
  return (
    <span
      className={cn(
        "px-1.5 py-px rounded text-[9px] font-mono font-medium",
        pct >= 70
          ? "bg-emerald-500/15 text-ok"
          : pct >= 30
            ? "bg-amber-500/15 text-warn"
            : "bg-muted/15 text-muted",
      )}
    >
      {pct}%
    </span>
  );
}

/** 调用用途徽标：辅助调用（任务/反思）首轮低命中是结构下限，与主对话区分展示 */
function KindBadge({ kind }: { kind?: string }) {
  const { t } = useTranslation("context");
  if (!kind || kind === "reply") return null;
  return (
    <span className="px-1.5 py-px rounded text-[9px] font-medium bg-accent/15 text-accent">
      {t(`history.kind.${kind}`, { defaultValue: kind })}
    </span>
  );
}

export function HistoryTab() {
  const { t } = useTranslation("context");
  const queryClient = useQueryClient();
  const [selected, setSelected] = useState<string | null>(null);
  const list = useQuery({
    queryKey: ["snapshots-list"],
    queryFn: () => contextApi.snapshotsList().then((r) => r.data),
    throwOnError: false,
  });
  const details = useQuery({
    queryKey: ["context", "history", selected],
    queryFn: selected ? () => contextApi.snapshotDetail(selected).then((r) => r.data) : skipToken,
    throwOnError: false,
  });
  const detail = details.data;

  const deleteMutation = useMutation({
    mutationFn: (filename: string) => contextApi.snapshotDelete(filename),
    onSuccess: (_, filename) => {
      queryClient.invalidateQueries({ queryKey: ["snapshots-list"] });
      queryClient.removeQueries({ queryKey: ["context", "history", filename] });
      if (selected === filename) setSelected(null);
    },
  });

  const clearAllMutation = useMutation({
    mutationFn: () => contextApi.snapshotsClear(),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["snapshots-list"] });
      setSelected(null);
      queryClient.removeQueries({ queryKey: ["context", "history"] });
    },
  });

  if (selected) {
    return (
      <div className="space-y-4">
        <div className="flex items-center justify-between">
          <button onClick={() => setSelected(null)} className="min-h-10 text-xs text-accent hover:underline">
            ← {t("history.back")}
          </button>
          {detail && <button
            onClick={() => downloadJson(detail, selected)}
            className="flex items-center gap-1 px-2 py-1 rounded-md text-[10px] text-muted hover:text-accent hover:bg-accent-subtle transition-colors"
          >
            <Download size={11} /> {t("monitor.export")}
          </button>}
        </div>
        {details.isPending && <PageSkeleton />}
        {details.error && <QueryError error={details.error} retry={() => void details.refetch()} />}
        {detail && <SnapshotDetail snapshot={detail} />}
      </div>
    );
  }

  if (list.isPending) return <PageSkeleton />;
  if (list.error && !list.data) return <QueryError error={list.error} retry={() => void list.refetch()} />;
  const snapshots = list.data?.snapshots ?? [];

  return (
    <div className="space-y-3">
      {list.error && <QueryError error={list.error} retry={() => void list.refetch()} />}
      {(deleteMutation.error || clearAllMutation.error) && <QueryError compact error={deleteMutation.error ?? clearAllMutation.error} />}
      <div className="flex items-center justify-between">
        <span className="text-xs text-muted">{t("history.count", { count: snapshots.length })}</span>
        {snapshots.length > 0 && (
          <button
            onClick={() => clearAllMutation.mutate()}
            disabled={clearAllMutation.isPending || deleteMutation.isPending}
            className="flex items-center gap-1 px-2 py-1 rounded-md text-[10px] text-muted hover:text-danger hover:bg-danger-subtle transition-colors"
          >
            <Trash2 size={11} /> {t("history.clearAll")}
          </button>
        )}
      </div>

      {snapshots.length === 0 ? (
        <p className="text-sm text-muted py-8 text-center">{t("history.empty")}</p>
      ) : (
        <div className="space-y-2">
          {snapshots.map((s: SnapshotListItem) => (
            <div key={s.filename} className="flex items-center gap-3 py-2.5 px-3 rounded-lg bg-elevated border border-border hover:border-border-strong transition-colors group">
              <button onClick={() => setSelected(s.filename)} className="flex-1 min-w-0 text-left">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="text-xs font-mono text-foreground">{s.model}</span>
                  <KindBadge kind={s.kind} />
                  <CacheHitBadge rate={s.cache_hit_rate} prefixStable={s.prefix_stable} legalBreak={s.legal_break} />
                  <span className="text-[10px] text-muted">{new Date(s.captured_at * 1000).toLocaleString()}</span>
                </div>
                <div className="flex flex-wrap items-center gap-3 mt-1 text-[10px] text-muted">
                  <span>{s.message_count} msgs</span>
                  <span>{s.tool_count} tools</span>
                  <span>~{s.estimated_tokens}t</span>
                  {s.model_context_window > 0 && (
                    <span>{Math.round((s.estimated_tokens / s.model_context_window) * 100)}% ctx</span>
                  )}
                  {s.cache_read_input_tokens != null && s.cache_read_input_tokens > 0 && (
                    <span className="text-ok">
                      {t("history.cacheRead", { tokens: s.cache_read_input_tokens.toLocaleString() })}
                    </span>
                  )}
                </div>
              </button>
              <ChevronRight size={14} className="text-muted opacity-0 group-hover:opacity-100 transition-opacity" />
              <button
                onClick={() => deleteMutation.mutate(s.filename)}
                aria-label={t("common:delete")}
                disabled={deleteMutation.isPending || clearAllMutation.isPending}
                className="p-2 rounded text-muted hover:text-danger transition-colors"
              >
                <Trash2 size={12} />
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
