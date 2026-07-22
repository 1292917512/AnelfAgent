import { useState, useEffect, useRef, type ReactNode } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { statusApi } from "@/lib/api";
import type { LogEntry } from "@/lib/types";
import { Card } from "@/components/common/Card";
import { cn } from "@/lib/utils";
import { Search, Pause, Play, Trash2, ArrowDownToLine, OctagonAlert } from "lucide-react";

const LEVELS = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] as const;
const ERROR_LEVELS = new Set(["ERROR", "CRITICAL"]);

const LEVEL_BADGE: Record<string, string> = {
  DEBUG: "bg-secondary text-muted border-border",
  INFO: "bg-[rgba(59,130,246,0.12)] text-info border-[rgba(59,130,246,0.3)]",
  WARNING: "bg-warn-subtle text-warn border-[rgba(245,158,11,0.3)]",
  ERROR: "bg-danger-subtle text-danger border-[rgba(239,68,68,0.3)]",
  CRITICAL: "bg-danger-subtle text-danger border-[rgba(239,68,68,0.3)] font-bold",
};

const LEVEL_CHIP: Record<string, string> = {
  DEBUG: "border-border text-muted",
  INFO: "border-[rgba(59,130,246,0.4)] text-info",
  WARNING: "border-[rgba(245,158,11,0.4)] text-warn",
  ERROR: "border-[rgba(239,68,68,0.4)] text-danger",
  CRITICAL: "border-[rgba(239,68,68,0.4)] text-danger",
};

const MAX_LOG_ENTRIES = 2000;
const BOTTOM_THRESHOLD = 48;

/** 关键词高亮渲染 */
function Highlighted({ text, keyword }: { text: string; keyword: string }) {
  if (!keyword) return <>{text}</>;
  const lower = text.toLowerCase();
  const kw = keyword.toLowerCase();
  const parts: ReactNode[] = [];
  let i = 0;
  let k = 0;
  for (;;) {
    const idx = lower.indexOf(kw, i);
    if (idx === -1) {
      parts.push(text.slice(i));
      break;
    }
    if (idx > i) parts.push(text.slice(i, idx));
    parts.push(
      <mark key={k++} className="bg-accent-subtle text-accent rounded-sm px-0.5">
        {text.slice(idx, idx + kw.length)}
      </mark>,
    );
    i = idx + kw.length;
  }
  return <>{parts}</>;
}

export function LogsPanel() {
  const { t } = useTranslation("status");
  const [levels, setLevels] = useState<Set<string>>(new Set(LEVELS));
  const [onlyErrors, setOnlyErrors] = useState(false);
  const [tag, setTag] = useState("");
  const [keyword, setKeyword] = useState("");
  const [logs, setLogs] = useState<LogEntry[]>([]);
  const [paused, setPaused] = useState(false);
  const [following, setFollowing] = useState(true);
  const [pendingCount, setPendingCount] = useState(0);

  const pausedRef = useRef(paused);
  pausedRef.current = paused;
  const followingRef = useRef(following);
  followingRef.current = following;
  const backlogRef = useRef<LogEntry[]>([]);
  const scrollRef = useRef<HTMLDivElement>(null);

  const queryClient = useQueryClient();
  const { data: stats } = useQuery({
    queryKey: ["logStats"],
    queryFn: () => statusApi.logStats().then((r) => r.data),
    refetchInterval: 10000,
  });

  useEffect(() => {
    let es: EventSource | null = null;
    statusApi.logs("", "", "", 500).then((r) => {
      setLogs(r.data.logs ?? []);
      es = new EventSource("/api/status/logs/stream");
      es.addEventListener("log", (e) => {
        try {
          const entry = JSON.parse(e.data) as LogEntry;
          if (pausedRef.current) {
            backlogRef.current.push(entry);
            setPendingCount(backlogRef.current.length);
            return;
          }
          setLogs((prev) => {
            const next = [...prev, entry];
            return next.length > MAX_LOG_ENTRIES ? next.slice(-MAX_LOG_ENTRIES) : next;
          });
        } catch { /* 忽略非日志帧 */ }
      });
      es.addEventListener("ping", () => {});
    });
    return () => { es?.close(); };
  }, []);

  useEffect(() => {
    if (following && !paused && scrollRef.current) {
      requestAnimationFrame(() => {
        if (scrollRef.current) scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
      });
    }
  }, [logs, following, paused]);

  const handleScroll = () => {
    const el = scrollRef.current;
    if (!el) return;
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < BOTTOM_THRESHOLD;
    if (atBottom !== followingRef.current) setFollowing(atBottom);
  };

  const togglePause = () => {
    if (paused) {
      const backlog = backlogRef.current;
      backlogRef.current = [];
      setPendingCount(0);
      if (backlog.length > 0) {
        setLogs((prev) => {
          const next = [...prev, ...backlog];
          return next.length > MAX_LOG_ENTRIES ? next.slice(-MAX_LOG_ENTRIES) : next;
        });
      }
      setPaused(false);
    } else {
      setPaused(true);
    }
  };

  const clearLogs = async () => {
    if (!confirm(t("logsView.clearConfirm"))) return;
    try {
      await statusApi.clearLogs();
    } catch { /* 后端清除失败时仍清理本地视图 */ }
    setLogs([]);
    backlogRef.current = [];
    setPendingCount(0);
    queryClient.invalidateQueries({ queryKey: ["logStats"] });
  };

  const toggleLevel = (lv: string) => {
    setOnlyErrors(false);
    setLevels((prev) => {
      const next = new Set(prev);
      if (next.has(lv)) next.delete(lv);
      else next.add(lv);
      return next;
    });
  };

  const toggleOnlyErrors = () => {
    if (onlyErrors) {
      setOnlyErrors(false);
      setLevels(new Set(LEVELS));
    } else {
      setOnlyErrors(true);
      setLevels(new Set(ERROR_LEVELS));
    }
  };

  const kw = keyword.trim();
  const filtered = logs.filter((e) => {
    if (!levels.has(e.level)) return false;
    if (tag && e.tag !== tag) return false;
    if (kw && !e.message.toLowerCase().includes(kw.toLowerCase())) return false;
    return true;
  });

  const byLevel = stats?.by_level ?? {};
  const byTag = stats?.by_tag ?? {};
  const tagOptions = Object.keys(byTag).sort();

  const statusLabel = paused
    ? t("paused")
    : following
      ? t("realtime")
      : t("logsView.unfollowed");

  return (
    <div className="space-y-3">
      {/* 级别多选 + 过滤器 */}
      <div className="flex flex-wrap items-center gap-2">
        <div className="flex flex-wrap items-center gap-1.5">
          {LEVELS.map((lv) => {
            const active = levels.has(lv);
            return (
              <button
                key={lv}
                onClick={() => toggleLevel(lv)}
                className={cn(
                  "px-2 py-1 text-[11px] font-medium rounded-full border transition-all",
                  active ? cn(LEVEL_CHIP[lv], "bg-card") : "border-border text-muted opacity-40",
                )}
              >
                {t(`levelLabels.${lv.toLowerCase()}`)}
                {byLevel[lv] != null && <span className="ml-1 opacity-70">{byLevel[lv]}</span>}
              </button>
            );
          })}
          <button
            onClick={toggleOnlyErrors}
            className={cn(
              "flex items-center gap-1 px-2 py-1 text-[11px] font-medium rounded-full border transition-all",
              onlyErrors
                ? "border-[rgba(239,68,68,0.5)] text-danger bg-danger-subtle"
                : "border-border text-muted hover:text-foreground",
            )}
          >
            <OctagonAlert size={11} /> {t("logsView.onlyErrors")}
          </button>
        </div>

        <select
          value={tag}
          onChange={(e) => setTag(e.target.value)}
          className="bg-elevated border border-input rounded-md px-2 py-1 text-xs text-foreground outline-none"
        >
          <option value="">{t("allTags")}</option>
          {tagOptions.map((tagOpt) => (
            <option key={tagOpt} value={tagOpt}>
              {tagOpt} ({byTag[tagOpt]})
            </option>
          ))}
        </select>

        <div className="relative flex-1 min-w-[140px] max-w-xs">
          <Search size={14} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-muted" />
          <input
            value={keyword}
            onChange={(e) => setKeyword(e.target.value)}
            placeholder={t("searchKeyword")}
            className="w-full pl-8 pr-3 py-1.5 text-xs bg-elevated border border-input rounded-md text-foreground outline-none focus:border-ring"
          />
        </div>

        <div className="flex items-center gap-1.5 ml-auto">
          <button
            onClick={togglePause}
            title={paused ? t("logsView.resume") : t("logsView.pause")}
            className={cn(
              "flex items-center gap-1 px-2.5 py-1.5 text-[11px] font-medium rounded-md border transition-all",
              paused
                ? "bg-warn-subtle text-warn border-[var(--warn)]"
                : "bg-secondary text-muted border-border hover:text-foreground",
            )}
          >
            {paused ? <Play size={12} /> : <Pause size={12} />}
            {paused ? `${t("logsView.resume")}${pendingCount > 0 ? ` (+${pendingCount})` : ""}` : t("logsView.pause")}
          </button>
          <button
            onClick={() => setFollowing(true)}
            title={t("logsView.follow")}
            className={cn(
              "p-1.5 rounded-md border transition-all",
              following
                ? "bg-accent-subtle text-accent border-accent"
                : "bg-secondary text-muted border-border hover:text-foreground",
            )}
          >
            <ArrowDownToLine size={14} />
          </button>
          <button
            onClick={clearLogs}
            title={t("logsView.clear")}
            className="p-1.5 rounded-md border bg-secondary text-muted border-border hover:text-danger transition-all"
          >
            <Trash2 size={14} />
          </button>
        </div>
      </div>

      {/* 状态行 */}
      <div className="flex items-center gap-2 text-[11px] text-muted">
        <span
          className={cn(
            "inline-block w-1.5 h-1.5 rounded-full",
            paused ? "bg-warn" : following ? "bg-ok animate-pulse" : "bg-muted",
          )}
        />
        <span>{statusLabel}</span>
        <span>·</span>
        <span>
          {t("totalLogs")} {filtered.length}/{logs.length} {t("entries")}
        </span>
        {stats && (
          <>
            <span>·</span>
            <span>{t("logsView.bufferUsage", { used: stats.total, capacity: stats.capacity })}</span>
          </>
        )}
      </div>

      {/* 日志列表 */}
      <Card className="!p-0 overflow-hidden">
        <div
          ref={scrollRef}
          onScroll={handleScroll}
          className="max-h-[560px] overflow-y-auto font-mono text-[11px] sm:text-[12px] py-1"
        >
          {filtered.length === 0 && (
            <p className="text-sm text-muted py-8 text-center font-sans">{t("noMatchingLogs")}</p>
          )}
          {filtered.map((entry, i) => (
            <div
              key={`${entry.time}-${i}`}
              className="flex items-start gap-2 py-1 px-3 hover:bg-hover transition-colors"
            >
              <span className="text-muted flex-shrink-0 w-16">{entry.time}</span>
              <span
                className={cn(
                  "flex-shrink-0 w-[68px] text-center px-1 py-px rounded border text-[10px] leading-4",
                  LEVEL_BADGE[entry.level] ?? "bg-secondary text-foreground border-border",
                )}
              >
                {entry.level}
              </span>
              {entry.tag && (
                <span className="flex-shrink-0 px-1.5 py-px rounded text-[10px] leading-4 bg-secondary text-muted border border-border max-w-24 truncate">
                  {entry.tag}
                </span>
              )}
              <span className="text-foreground break-all min-w-0">
                <Highlighted text={entry.message} keyword={kw} />
              </span>
            </div>
          ))}
        </div>
      </Card>
    </div>
  );
}
