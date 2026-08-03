import { useState, useEffect, useRef } from "react";
import { useSearchParams } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { statusApi } from "@/lib/api";
import type { LogEntry } from "@/lib/types";
import { ConfirmDialog } from "@/components/ui";
import { cn } from "@/lib/utils";
import { LogsToolbar } from "./LogsToolbar";
import { LogList, type LogRow } from "./LogList";
const LEVELS = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] as const;
const ERROR_LEVELS = new Set(["ERROR", "CRITICAL"]);
const MAX_LOG_ENTRIES = 2000;
const BOTTOM_THRESHOLD = 48;
/** 高亮定位的持续时长（毫秒） */
const HIGHLIGHT_DURATION_MS = 2500;export function LogsPanel() {
  const { t } = useTranslation("status");
  const [searchParams] = useSearchParams();
  const [levels, setLevels] = useState<Set<string>>(new Set(LEVELS));
  const [onlyErrors, setOnlyErrors] = useState(false);
  const [tag, setTag] = useState("");
  const [keyword, setKeyword] = useState("");
  const [logs, setLogs] = useState<LogRow[]>([]);
  const [paused, setPaused] = useState(false);
  const [following, setFollowing] = useState(true);
  const [pendingCount, setPendingCount] = useState(0);
  const [confirmClear, setConfirmClear] = useState(false);
  const [highlightSeq, setHighlightSeq] = useState<number | null>(null);  const pausedRef = useRef(paused);
  pausedRef.current = paused;
  const followingRef = useRef(following);
  followingRef.current = following;
  const backlogRef = useRef<LogRow[]>([]);
  const seqRef = useRef(0);
  const scrollRef = useRef<HTMLDivElement>(null);
  const rowRef = useRef<HTMLDivElement>(null);
  /** 跳转定位的消息内容（URL ?jump= 传入，仅消费一次） */
  const jumpMsgRef = useRef<string | null>(searchParams.get("jump"));  const queryClient = useQueryClient();
  const { data: stats } = useQuery({
    queryKey: ["logStats"],
    queryFn: () => statusApi.logStats().then((r) => r.data),
    refetchInterval: 10000,
  });  useEffect(() => {
    let cancelled = false;
    let es: EventSource | null = null;
    // 初始加载整个日志缓冲区（与后端容量一致），避免更早的日志不可见
    statusApi.logs("", "", "", MAX_LOG_ENTRIES).then((r) => {
      if (cancelled) return;
      const rows = (r.data.logs ?? []).map((e) => ({ ...e, seq: ++seqRef.current }));
      setLogs(rows);
      // 跳转定位：从「需要注意」进入时按消息内容定位最新匹配条目并高亮
      // （Dashboard 切换标签页时会清除查询参数，无需在此移除 jump）
      const jumpMsg = jumpMsgRef.current;
      jumpMsgRef.current = null;
      if (jumpMsg) {
        // 从最新往前找首个匹配条目；先取消跟随，避免下方自动滚底 effect 抵消定位
        let target: LogRow | undefined;
        for (let i = rows.length - 1; i >= 0; i--) {
          const row = rows[i];
          if (row && row.message === jumpMsg) {
            target = row;
            break;
          }
        }
        setFollowing(false);
        if (target) setHighlightSeq(target.seq);
      }
      es = new EventSource("/api/status/logs/stream");
      es.addEventListener("log", (e) => {
        try {
          const entry = { ...(JSON.parse(e.data) as LogEntry), seq: ++seqRef.current };
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
    return () => {
      cancelled = true;
      es?.close();
    };
  }, []);  useEffect(() => {
    if (following && !paused && scrollRef.current) {
      requestAnimationFrame(() => {
        if (scrollRef.current) scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
      });
    }
  }, [logs, following, paused]);  // 高亮行渲染后滚动至可见，短暂停留后取消高亮
  useEffect(() => {
    if (highlightSeq === null) return;
    const el = rowRef.current;
    if (el) {
      requestAnimationFrame(() => el.scrollIntoView({ block: "center" }));
    }
    const timer = setTimeout(() => setHighlightSeq(null), HIGHLIGHT_DURATION_MS);
    return () => clearTimeout(timer);
  }, [highlightSeq]);  const handleScroll = () => {
    const el = scrollRef.current;
    if (!el) return;
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < BOTTOM_THRESHOLD;
    if (atBottom !== followingRef.current) setFollowing(atBottom);
  };  const togglePause = () => {
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
  };  const clearLogs = async () => {
    setConfirmClear(false);
    try {
      await statusApi.clearLogs();
    } catch { /* 后端清除失败时仍清理本地视图 */ }
    setLogs([]);
    backlogRef.current = [];
    setPendingCount(0);
    queryClient.invalidateQueries({ queryKey: ["logStats"] });
  };  const toggleLevel = (lv: string) => {
    setOnlyErrors(false);
    setLevels((prev) => {
      const next = new Set(prev);
      if (next.has(lv)) next.delete(lv);
      else next.add(lv);
      return next;
    });
  };  const toggleOnlyErrors = () => {
    if (onlyErrors) {
      setOnlyErrors(false);
      setLevels(new Set(LEVELS));
    } else {
      setOnlyErrors(true);
      setLevels(new Set(ERROR_LEVELS));
    }
  };  const kw = keyword.trim();
  const filtered = logs.filter((e) => {
    if (!levels.has(e.level)) return false;
    if (tag && e.tag !== tag) return false;
    if (kw && !e.message.toLowerCase().includes(kw.toLowerCase())) return false;
    return true;
  });  const byLevel = stats?.by_level ?? {};
  const byTag = stats?.by_tag ?? {};
  const tagOptions = Object.keys(byTag).sort();  const statusLabel = paused
    ? t("paused")
    : following
      ? t("realtime")
      : t("logsView.unfollowed");  return (
    <div className="space-y-3">
      <LogsToolbar
        levels={levels}
        onToggleLevel={toggleLevel}
        byLevel={byLevel}
        onlyErrors={onlyErrors}
        onToggleOnlyErrors={toggleOnlyErrors}
        tag={tag}
        onTagChange={setTag}
        tagOptions={tagOptions}
        byTag={byTag}
        keyword={keyword}
        onKeywordChange={setKeyword}
        paused={paused}
        onTogglePause={togglePause}
        pendingCount={pendingCount}
        following={following}
        onFollow={() => setFollowing(true)}
        onClear={() => setConfirmClear(true)}
      />      {/* 状态行 */}
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
      </div>      <LogList
        filtered={filtered}
        keyword={kw}
        scrollRef={scrollRef}
        onScroll={handleScroll}
        highlightSeq={highlightSeq}
        rowRef={rowRef}
      />      <ConfirmDialog
        open={confirmClear}
        onClose={() => setConfirmClear(false)}
        onConfirm={clearLogs}
        title={t("logsView.clear")}
        message={t("logsView.clearConfirm")}
        confirmText={t("logsView.clear")}
        cancelText={t("common:cancel")}
        danger
      />
    </div>
  );
}
