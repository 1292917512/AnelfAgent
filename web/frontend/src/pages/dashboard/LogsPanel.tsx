import { useState, useEffect, useRef } from "react";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { statusApi } from "@/lib/api";
import { StatCard } from "@/components/common/StatCard";
import { Card } from "@/components/common/Card";
import { cn } from "@/lib/utils";
import { Search, Pause, Play } from "lucide-react";

const LEVEL_OPTIONS = ["", "DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"];
const LEVEL_COLORS: Record<string, string> = {
  DEBUG: "text-[var(--muted)]",
  INFO: "text-[var(--info)]",
  WARNING: "text-[var(--warn)]",
  ERROR: "text-[var(--danger)]",
  CRITICAL: "text-[var(--danger)] font-bold",
};

type LogEntry = { level: string; message: string; tag: string; time: string };

const MAX_LOG_ENTRIES = 2000;

export function LogsPanel() {
  const { t } = useTranslation("status");
  const [level, setLevel] = useState("");
  const [tag, setTag] = useState("");
  const [keyword, setKeyword] = useState("");
  const [logs, setLogs] = useState<LogEntry[]>([]);
  const [paused, setPaused] = useState(false);
  const pausedRef = useRef(paused);
  pausedRef.current = paused;
  const scrollRef = useRef<HTMLDivElement>(null);

  const { data: stats } = useQuery({ queryKey: ["logStats"], queryFn: () => statusApi.logStats().then((r) => r.data), refetchInterval: 10000 });

  useEffect(() => {
    let es: EventSource | null = null;
    statusApi.logs("", "", "", 500).then((r) => {
      setLogs(r.data.logs ?? []);
      es = new EventSource("/api/status/logs/stream");
      es.addEventListener("log", (e) => {
        if (pausedRef.current) return;
        try {
          const entry = JSON.parse(e.data) as LogEntry;
          setLogs((prev) => { const next = [...prev, entry]; return next.length > MAX_LOG_ENTRIES ? next.slice(-MAX_LOG_ENTRIES) : next; });
        } catch { /* ignore */ }
      });
      es.addEventListener("ping", () => {});
    });
    return () => { es?.close(); };
  }, []);

  useEffect(() => {
    if (!paused && scrollRef.current) {
      requestAnimationFrame(() => { if (scrollRef.current) scrollRef.current.scrollTop = scrollRef.current.scrollHeight; });
    }
  }, [logs, paused]);

  const filtered = logs.filter((e) => {
    if (level && e.level !== level) return false;
    if (tag && e.tag !== tag) return false;
    if (keyword && !e.message.toLowerCase().includes(keyword.toLowerCase())) return false;
    return true;
  });

  const byLevel = (stats?.by_level ?? {}) as Record<string, number>;
  const byTag = (stats?.by_tag ?? {}) as Record<string, number>;
  const tagOptions = Object.keys(byTag).sort();

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap gap-3">
        <StatCard label={t("totalLogs")} value={String(logs.length)} className="flex-1 min-w-[100px]" />
        {Object.entries(byLevel).map(([lv, cnt]) => (
          <StatCard key={lv} label={lv} value={String(cnt)}
            variant={lv === "ERROR" || lv === "CRITICAL" ? "danger" : lv === "WARNING" ? "warn" : "default"}
            className="flex-1 min-w-[80px]" />
        ))}
      </div>

      <div className="flex flex-wrap gap-2 items-center">
        <select value={level} onChange={(e) => setLevel(e.target.value)}
          className="bg-[var(--bg-elevated)] border border-[var(--input)] rounded-[var(--radius-md)] px-2 py-1 text-xs text-[var(--text)] outline-none">
          {LEVEL_OPTIONS.map((lv) => (
            <option key={lv} value={lv}>{t(`levelLabels.${lv === "" ? "all" : lv.toLowerCase()}`)}</option>
          ))}
        </select>

        <select value={tag} onChange={(e) => setTag(e.target.value)}
          className="bg-[var(--bg-elevated)] border border-[var(--input)] rounded-[var(--radius-md)] px-2 py-1 text-xs text-[var(--text)] outline-none">
          <option value="">{t("allTags")}</option>
          {tagOptions.map((tagOpt) => (
            <option key={tagOpt} value={tagOpt}>{tagOpt} ({byTag[tagOpt]})</option>
          ))}
        </select>

        <div className="relative flex-1 min-w-[150px] max-w-xs">
          <Search size={14} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-[var(--muted)]" />
          <input value={keyword} onChange={(e) => setKeyword(e.target.value)} placeholder={t("searchKeyword")}
            className="w-full pl-8 pr-3 py-1.5 text-xs bg-[var(--bg-elevated)] border border-[var(--input)] rounded-[var(--radius-md)] text-[var(--text)] outline-none focus:border-[var(--ring)]" />
        </div>

        <button onClick={() => setPaused(!paused)}
          className={cn("flex items-center gap-1 px-3 py-1 text-[11px] font-medium rounded-full border transition-all",
            paused ? "bg-[var(--warn-subtle)] text-[var(--warn)] border-[var(--warn)]" : "bg-[var(--ok-subtle)] text-[var(--ok)] border-[rgba(34,197,94,0.3)]")}>
          {paused ? <><Play size={12} /> {t("paused")}</> : <><Pause size={12} /> {t("realtime")}</>}
        </button>
      </div>

      <Card title={`${t("logs")} (${filtered.length} ${t("entries")}${paused ? ` · ${t("paused")}` : ` · ${t("realtime")}`})`}>
        <div ref={scrollRef} className="space-y-0.5 max-h-[500px] overflow-y-auto font-mono text-[12px]">
          {filtered.length === 0 && <p className="text-sm text-[var(--muted)] py-4 text-center">{t("noMatchingLogs")}</p>}
          {filtered.map((entry, i) => (
            <div key={`${entry.time}-${entry.level}-${i}`} className="flex items-start gap-2 py-1 px-2 rounded-[var(--radius-sm)] hover:bg-[var(--bg-hover)] transition-colors">
              <span className="text-[var(--muted)] flex-shrink-0 w-16">{entry.time}</span>
              <span className={cn("flex-shrink-0 w-16 font-semibold", LEVEL_COLORS[entry.level] ?? "text-[var(--text)]")}>{entry.level}</span>
              {entry.tag && <span className="flex-shrink-0 px-1.5 py-0.5 rounded text-[10px] bg-[var(--secondary)] text-[var(--muted)] border border-[var(--border)]">{entry.tag}</span>}
              <span className="text-[var(--text)] break-all">{entry.message}</span>
            </div>
          ))}
        </div>
      </Card>
    </div>
  );
}
