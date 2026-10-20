import { useEffect, useState } from "react";

/** 仅在启用且页面可见时刷新经过时间；恢复可见立即校准。 */
export function useNow(enabled = true, intervalMs = 1000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!enabled) return;
    let id: ReturnType<typeof setInterval> | undefined;
    const update = () => {
      clearInterval(id);
      if (!document.hidden) {
        setNow(Date.now());
        id = setInterval(() => setNow(Date.now()), intervalMs);
      }
    };
    update();
    document.addEventListener("visibilitychange", update);
    return () => { clearInterval(id); document.removeEventListener("visibilitychange", update); };
  }, [enabled, intervalMs]);
  return now;
}
