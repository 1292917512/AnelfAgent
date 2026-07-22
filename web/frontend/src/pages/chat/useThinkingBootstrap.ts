import { useEffect } from "react";
import { thinkingApi } from "@/lib/api";
import { useThinkingStore } from "@/stores/thinking-store";

/** 确保思维链 SSE 已启动（与 Thinking 页共享全局单例，幂等） */
export function useThinkingBootstrap() {
  const _statusSynced = useThinkingStore((s) => s._statusSynced);
  const setEnabled = useThinkingStore((s) => s.setEnabled);
  const setStatusSynced = useThinkingStore((s) => s.setStatusSynced);
  const startSSE = useThinkingStore((s) => s.startSSE);

  useEffect(() => {
    if (_statusSynced) return;
    thinkingApi.status().then((r) => {
      setEnabled(r.data.enabled);
      if (r.data.enabled) startSSE();
      setStatusSynced(true);
    }).catch((e) => console.warn("[API]", e));
  }, [_statusSynced, setEnabled, setStatusSynced, startSSE]);
}
