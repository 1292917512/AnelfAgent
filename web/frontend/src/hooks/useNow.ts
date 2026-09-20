import { useEffect, useState } from "react";

/** 每秒刷新一次的当前时间（epoch 毫秒；enabled=false 时暂停计时） */
export function useNow(enabled = true, intervalMs = 1000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!enabled) return;
    const id = setInterval(() => setNow(Date.now()), intervalMs);
    return () => clearInterval(id);
  }, [enabled, intervalMs]);
  return now;
}
