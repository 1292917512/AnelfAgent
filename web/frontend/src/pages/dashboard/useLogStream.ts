import { useCallback, useEffect, useRef, useState } from "react";
import type { LogEntry } from "@/lib/types";
import { MAX_LOG_ENTRIES, mergeLogs } from "./log-buffer";

export function useLogStream() {
  const [logs, setLogs] = useState<LogEntry[]>([]);
  const [connection, setConnection] = useState<"connecting" | "live" | "reconnecting">("connecting");
  const [paused, setPaused] = useState(false);
  const [pendingCount, setPendingCount] = useState(0);
  const [attempt, setAttempt] = useState(0);
  const pausedRef = useRef(false);
  const queue = useRef<LogEntry[]>([]);
  const snapshot = useRef<LogEntry[] | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const flush = useCallback(() => {
    if (timer.current !== null) { clearTimeout(timer.current); timer.current = null; }
    if (pausedRef.current) {
      setPendingCount(Math.min(MAX_LOG_ENTRIES, queue.current.length + (snapshot.current?.length ?? 0)));
      return;
    }
    const incoming = queue.current;
    const replacement = snapshot.current;
    queue.current = [];
    snapshot.current = null;
    setLogs((current) => mergeLogs(replacement ?? current, incoming));
    setPendingCount(0);
  }, []);

  useEffect(() => {
    const stream = new EventSource("/api/status/logs/stream");
    const schedule = () => { if (timer.current === null) timer.current = setTimeout(flush, 100); };
    stream.addEventListener("snapshot", (event: MessageEvent<string>) => {
      try {
        const data: { logs: LogEntry[] } = JSON.parse(event.data);
        snapshot.current = data.logs.slice(-MAX_LOG_ENTRIES);
        queue.current = [];
        setConnection("live");
        schedule();
      } catch { setConnection("reconnecting"); }
    });
    stream.addEventListener("log", (event: MessageEvent<string>) => {
      try {
        const entry: LogEntry = JSON.parse(event.data);
        queue.current.push(entry);
        if (queue.current.length > MAX_LOG_ENTRIES) queue.current = queue.current.slice(-MAX_LOG_ENTRIES);
        schedule();
      } catch { setConnection("reconnecting"); }
    });
    stream.onerror = () => setConnection("reconnecting");
    return () => {
      stream.close();
      if (timer.current !== null) clearTimeout(timer.current);
      timer.current = null;
      queue.current = [];
      snapshot.current = null;
    };
  }, [attempt, flush]);

  const togglePause = () => {
    pausedRef.current = !pausedRef.current;
    setPaused(pausedRef.current);
    if (!pausedRef.current) flush();
  };
  const reconnect = () => {
    setConnection("connecting");
    setAttempt((value) => value + 1);
  };
  const reset = () => {
    queue.current = [];
    snapshot.current = null;
    pausedRef.current = false;
    setPaused(false);
    setPendingCount(0);
    setLogs([]);
    reconnect();
  };
  return { logs, paused, pendingCount, connection, togglePause, reconnect, reset };
}
