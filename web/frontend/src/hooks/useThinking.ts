import { useEffect } from "react";
import { useThinkingStore } from "@/stores/thinking-store";

let observers = 0;
let timer: ReturnType<typeof setInterval> | undefined;

/** 观察面板共享状态轮询；最后一个观察者离开时停止计时。 */
export function useThinkingBootstrap(): void {
  useEffect(() => {
    if (observers++ === 0) {
      void useThinkingStore.getState().initialize();
      timer = setInterval(() => { if (!document.hidden) void useThinkingStore.getState().initialize(true); }, 15_000);
    }
    return () => { if (--observers === 0) { clearInterval(timer); timer = undefined; } };
  }, []);
}

export function useThinkingSessions(): void {
  useThinkingBootstrap();
  const refresh = useThinkingStore((state) => state.refreshSessions);
  useEffect(() => { void refresh(); }, [refresh]);
}
