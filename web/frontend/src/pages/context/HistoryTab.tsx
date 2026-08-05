import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Download, Trash2, ChevronRight } from "lucide-react";
import { contextApi } from "@/lib/api";
import { SnapshotDetail } from "@/components/context/SnapshotDetail";
import { cn } from "@/lib/utils";
import type { ContextSnapshotData, SnapshotListItem } from "@/lib/types";
import { downloadJson } from "./downloadJson";

/** 缓存命中率徽标：≥70% 绿 / ≥30% 黄 / 其余灰；无数据显示 — */
function CacheHitBadge({ rate }: { rate?: number | null }) {
  if (rate == null) return null;
  const pct = Math.round(rate * 100);
  return (
    <span
      className={cn(
        "px-1.5 py-px rounded text-[9px] font-mono font-medium",
        pct >= 70
          ? "bg-emerald-500/15 text-emerald-500"
          : pct >= 30
            ? "bg-amber-500/15 text-amber-500"
            : "bg-muted/15 text-muted",
      )}
    >
      {pct}%
    </span>
  );
}

export function HistoryTab() {
  const { t } = useTranslation("context");
  const queryClient = useQueryClient();
  const [selected, setSelected] = useState<string | null>(null);
  const [detail, setDetail] = useState<ContextSnapshotData | null>(null);

  const { data: listData } = useQuery({
    queryKey: ["snapshots-list"],
    queryFn: () => contextApi.snapshotsList().then((r) => r.data),
  });

  const deleteMutation = useMutation({
    mutationFn: (filename: string) => contextApi.snapshotDelete(filename),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["snapshots-list"] });
      if (selected) { setSelected(null); setDetail(null); }
    },
  });

  const clearAllMutation = useMutation({
    mutationFn: () => contextApi.snapshotsClear(),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["snapshots-list"] });
      setSelected(null);
      setDetail(null);
    },
  });

  const handleOpen = async (filename: string) => {
    setSelected(filename);
    try {
      const r = await contextApi.snapshotDetail(filename);
      setDetail(r.data);
    } catch { setDetail(null); }
  };

  if (selected && detail) {
    return (
      <div className="space-y-4">
        <div className="flex items-center justify-between">
          <button onClick={() => { setSelected(null); setDetail(null); }} className="text-xs text-accent hover:underline">
            ← {t("history.back")}
          </button>
          <button
            onClick={() => downloadJson(detail, selected)}
            className="flex items-center gap-1 px-2 py-1 rounded-md text-[10px] text-muted hover:text-accent hover:bg-accent-subtle transition-colors"
          >
            <Download size={11} /> {t("monitor.export")}
          </button>
        </div>
        <SnapshotDetail snapshot={detail} />
      </div>
    );
  }

  const snapshots = listData?.snapshots ?? [];

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <span className="text-xs text-muted">{t("history.count", { count: snapshots.length })}</span>
        {snapshots.length > 0 && (
          <button
            onClick={() => clearAllMutation.mutate()}
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
              <button onClick={() => handleOpen(s.filename)} className="flex-1 min-w-0 text-left">
                <div className="flex items-center gap-2">
                  <span className="text-xs font-mono text-foreground">{s.model}</span>
                  <CacheHitBadge rate={s.cache_hit_rate} />
                  <span className="text-[10px] text-muted">{new Date(s.captured_at * 1000).toLocaleString()}</span>
                </div>
                <div className="flex items-center gap-3 mt-1 text-[10px] text-muted">
                  <span>{s.message_count} msgs</span>
                  <span>{s.tool_count} tools</span>
                  <span>~{s.estimated_tokens}t</span>
                  {s.model_context_window > 0 && (
                    <span>{Math.round((s.estimated_tokens / s.model_context_window) * 100)}% ctx</span>
                  )}
                  {s.cache_read_input_tokens != null && s.cache_read_input_tokens > 0 && (
                    <span className="text-emerald-500">
                      {t("history.cacheRead", { tokens: s.cache_read_input_tokens.toLocaleString() })}
                    </span>
                  )}
                </div>
              </button>
              <ChevronRight size={14} className="text-muted opacity-0 group-hover:opacity-100 transition-opacity" />
              <button
                onClick={() => deleteMutation.mutate(s.filename)}
                className="p-1 rounded text-muted hover:text-danger opacity-0 group-hover:opacity-100 transition-all"
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

// ======================================================================
// 上下文注入 Tab
// ======================================================================
