import { useCallback, useEffect, useRef } from "react";
import { useTranslation } from "react-i18next";
import { useMutation } from "@tanstack/react-query";
import { useShallow } from "zustand/react/shallow";
import { Camera, CameraOff, Download, Trash2 } from "lucide-react";
import { contextApi } from "@/lib/api";
import { useThinkingStore } from "@/stores/thinking-store";
import { SnapshotDetail } from "@/components/context/SnapshotDetail";
import { downloadJson } from "./downloadJson";

export function MonitorTab() {
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
          <div className="flex items-center gap-2">
            <button
              onClick={() => downloadJson(snapshotData, `context_snapshot_${new Date(snapshotData.captured_at * 1000).toISOString().slice(0, 19).replace(/:/g, "")}.json`)}
              className="flex items-center gap-1 px-2 py-1 rounded-md text-[10px] text-muted hover:text-accent hover:bg-accent-subtle transition-colors"
            >
              <Download size={11} /> {t("monitor.export")}
            </button>
            <button onClick={handleClear} className="flex items-center gap-1 px-2 py-1 rounded-md text-[10px] text-muted hover:text-danger hover:bg-danger-subtle transition-colors">
              <Trash2 size={11} /> {t("monitor.discard")}
            </button>
          </div>
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
