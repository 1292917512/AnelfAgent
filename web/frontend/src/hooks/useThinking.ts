import { useEffect } from "react";
import { useThinkingStore } from "@/stores/thinking-store";

/** Starts shared trace observation and loads the retained session list. */
export function useThinkingBootstrap(): void {
  const initialize = useThinkingStore((state) => state.initialize);
  useEffect(() => {
    void initialize();
    const timer = setInterval(() => { if (!document.hidden) void initialize(true); }, 15_000);
    return () => clearInterval(timer);
  }, [initialize]);
}

export function useThinkingSessions(): void {
  useThinkingBootstrap();
  const refresh = useThinkingStore((state) => state.refreshSessions);
  useEffect(() => { void refresh(); }, [refresh]);
}
