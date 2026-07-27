import { useCallback, useEffect, useRef, useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { useThinkingStore } from "@/stores/thinking-store";
import { contextApi } from "@/lib/api";
import { TabBar } from "@/components/common/TabBar";
import { Card } from "@/components/common/Card";
import { StatusDot } from "@/components/common/StatusDot";
import { SnapshotDetail } from "@/components/context/SnapshotDetail";
import { cn } from "@/lib/utils";
import type { ContextSnapshotData, ContextProviderStatus, SnapshotListItem } from "@/lib/types";
import { useShallow } from "zustand/react/shallow";
import {
  Camera, CameraOff, Trash2,
  Activity, History, Database, ChevronRight,
} from "lucide-react";

type ContextTab = "monitor" | "history" | "providers";

// ======================================================================
// 实时监控 Tab
// ======================================================================

function MonitorTab() {
  const { t } = useTranslation("context");
  const {
    snapshotArmed, snapshotData, showSnapshot,
    setSnapshotArmed, setSnapshotData, setShowSnapshot, clearSnapshot,
  } = useThinkingStore(useShallow((s) => ({
    snapshotArmed: s.snapshotArmed,
    snapshotData: s.snapshotData,
    showSnapshot: s.showSnapshot,
    setSnapshotArmed: s.setSnapshotArmed,
    setSnapshotData: s.setSnapshotData,
    setShowSnapshot: s.setShowSnapshot,
    clearSnapshot: s.clearSnapshot,
  })));
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const startPoll = useCallback(() => {
    if (pollRef.current) return;
    pollRef.current = setInterval(async () => {
      try {
        const r = await contextApi.snapshotGet();
        if (r.data.snapshot) {
          setSnapshotData(r.data.snapshot);
          setSnapshotArmed(false);
          setShowSnapshot(true);
          if (pollRef.current) clearInterval(pollRef.current);
          pollRef.current = null;
        }
      } catch { /* ignore */ }
    }, 800);
  }, [setSnapshotData, setSnapshotArmed, setShowSnapshot]);

  const armMutation = useMutation({
    mutationFn: () => contextApi.snapshotArm(),
    onSuccess: () => {
      setSnapshotArmed(true);
      setSnapshotData(null);
      setShowSnapshot(false);
      startPoll();
    },
  });

  const disarmMutation = useMutation({
    mutationFn: () => contextApi.snapshotDisarm(),
    onSuccess: () => {
      setSnapshotArmed(false);
      if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; }
    },
  });

  const handleClear = useCallback(() => {
    clearSnapshot();
    contextApi.snapshotClear().catch(() => {});
  }, [clearSnapshot]);

  // 进入页面时恢复轮询
  useEffect(() => {
    if (snapshotArmed) startPoll();
    return () => { if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; } };
  }, [snapshotArmed, startPoll]);

  // 已有快照时直接展示
  if (showSnapshot && snapshotData) {
    return (
      <div className="space-y-4">
        <div className="flex items-center justify-between">
          <span className="text-sm font-semibold text-heading">{t("monitor.captured")}</span>
          <button onClick={handleClear} className="flex items-center gap-1 px-2 py-1 rounded-md text-[10px] text-muted hover:text-danger hover:bg-danger-subtle transition-colors">
            <Trash2 size={11} /> {t("monitor.discard")}
          </button>
        </div>
        <SnapshotDetail snapshot={snapshotData} />
      </div>
    );
  }

  return (
    <div className="max-w-lg mx-auto space-y-6 py-8">
      <div className="text-center space-y-4">
        {snapshotArmed ? (
          <>
            <div className="inline-flex items-center justify-center w-16 h-16 rounded-full bg-accent-subtle animate-pulse">
              <Camera size={28} className="text-accent" />
            </div>
            <div>
              <p className="text-sm font-medium text-heading">{t("monitor.waiting")}</p>
              <p className="text-xs text-muted mt-1">{t("monitor.waitingDesc")}</p>
            </div>
            <button
              onClick={() => disarmMutation.mutate()}
              disabled={disarmMutation.isPending}
              className="flex items-center gap-1.5 px-4 py-2 rounded-md border border-border text-xs font-medium text-muted hover:text-danger hover:border-danger/30 transition-all mx-auto"
            >
              <CameraOff size={13} /> {t("monitor.cancel")}
            </button>
          </>
        ) : (
          <>
            <div className="inline-flex items-center justify-center w-16 h-16 rounded-full bg-elevated border border-border">
              <Camera size={28} className="text-muted" />
            </div>
            <div>
              <p className="text-sm font-medium text-heading">{t("monitor.ready")}</p>
              <p className="text-xs text-muted mt-1">{t("monitor.readyDesc")}</p>
            </div>
            <button
              onClick={() => armMutation.mutate()}
              disabled={armMutation.isPending}
              className="flex items-center gap-1.5 px-4 py-2 rounded-md bg-accent text-white text-xs font-medium hover:opacity-90 disabled:opacity-50 transition-all mx-auto"
            >
              <Camera size={13} /> {t("monitor.arm")}
            </button>
          </>
        )}
      </div>
    </div>
  );
}

// ======================================================================
// 快照历史 Tab
// ======================================================================

function HistoryTab() {
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
        <button onClick={() => { setSelected(null); setDetail(null); }} className="text-xs text-accent hover:underline">
          ← {t("history.back")}
        </button>
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
                  <span className="text-[10px] text-muted">{new Date(s.captured_at * 1000).toLocaleString()}</span>
                </div>
                <div className="flex items-center gap-3 mt-1 text-[10px] text-muted">
                  <span>{s.message_count} msgs</span>
                  <span>{s.tool_count} tools</span>
                  <span>~{s.estimated_tokens}t</span>
                  {s.model_context_window > 0 && (
                    <span>{Math.round((s.estimated_tokens / s.model_context_window) * 100)}% ctx</span>
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

function ProvidersTab() {
  const { t } = useTranslation("context");
  const { data } = useQuery({
    queryKey: ["context-providers"],
    queryFn: () => contextApi.providers().then((r) => r.data as ContextProviderStatus),
    refetchInterval: 3000,
  });

  if (!data || data.providers.length === 0) {
    return <p className="text-sm text-muted py-8 text-center">{t("providers.empty")}</p>;
  }

  const ratio = data.total_budget > 0 ? data.current_used / data.total_budget : 0;

  return (
    <div className="space-y-4">
      <Card title={t("providers.budget")}>
        <div className="flex items-center justify-between text-xs mb-1.5">
          <span className="text-muted">{t("providers.used")}</span>
          <span className={cn("font-mono font-medium", ratio >= 0.9 ? "text-danger" : ratio >= 0.7 ? "text-warn" : "text-ok")}>
            {data.current_used} / {data.total_budget}
          </span>
        </div>
        <div className="h-2 rounded-full bg-elevated overflow-hidden">
          <div
            className={cn("h-full rounded-full transition-all duration-500", ratio >= 0.9 ? "bg-danger" : ratio >= 0.7 ? "bg-warn" : "bg-ok")}
            style={{ width: `${Math.min(ratio * 100, 100)}%` }}
          />
        </div>
        <div className="flex items-center justify-between text-[10px] text-muted mt-1">
          <span>{t("providers.staticEstimate")}: {data.static_estimate}</span>
          <span>{t("providers.peak")}: {data.peak_used}</span>
        </div>
      </Card>

      <div className="space-y-2">
        {data.providers.map((p) => (
          <div key={p.name} className="flex items-center gap-3 py-2 px-3 rounded-lg bg-elevated border border-border">
            <StatusDot status={p.ready ? "ok" : "warn"} />
            <div className="flex-1 min-w-0">
              <span className="text-xs font-medium text-foreground">{p.name}</span>
              {p.description && <p className="text-[10px] text-muted truncate">{p.description}</p>}
              {p.last_error && <p className="text-[10px] text-danger truncate">{p.last_error}</p>}
            </div>
            <div className="flex items-center gap-3 text-[10px] font-mono text-muted flex-shrink-0">
              <span>{p.tokens}t</span>
              <span>{p.bytes}B</span>
              <span>{p.cost_ms.toFixed(1)}ms</span>
              <span>×{p.call_count}</span>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

// ======================================================================
// 主页面
// ======================================================================

interface ContextProps {
  hideHeader?: boolean;
}

export default function Context({ hideHeader = false }: ContextProps) {
  const { t } = useTranslation("context");
  const [tab, setTab] = useState<ContextTab>("monitor");

  const tabs = [
    { key: "monitor" as ContextTab, label: t("tabs.monitor"), icon: Activity },
    { key: "history" as ContextTab, label: t("tabs.history"), icon: History },
    { key: "providers" as ContextTab, label: t("tabs.providers"), icon: Database },
  ];

  return (
    <div className="h-full flex flex-col">
      {!hideHeader && (
        <>
          <div className="px-4 md:px-6 py-4 border-b border-border">
            <h1 className="text-lg font-semibold text-heading">{t("title")}</h1>
            <p className="text-xs text-muted mt-0.5">{t("subtitle")}</p>
          </div>
          <div className="px-4 md:px-6 border-b border-border">
            <TabBar tabs={tabs} activeTab={tab} onChange={setTab} />
          </div>
        </>
      )}
      {hideHeader && (
        <div className="px-4 md:px-6 border-b border-border">
          <TabBar tabs={tabs} activeTab={tab} onChange={setTab} />
        </div>
      )}
      <div className="flex-1 overflow-y-auto p-4 md:p-6">
        {tab === "monitor" && <MonitorTab />}
        {tab === "history" && <HistoryTab />}
        {tab === "providers" && <ProvidersTab />}
      </div>
    </div>
  );
}
