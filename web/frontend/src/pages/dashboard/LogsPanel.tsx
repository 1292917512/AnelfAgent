import { useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Radio, RefreshCw } from "lucide-react";
import { apiErrorMessage, statusApi } from "@/lib/api";
import { Button, ConfirmDialog, toast } from "@/components/ui";
import { LogsToolbar } from "./LogsToolbar";
import { LogList } from "./LogList";
import { LOG_LEVELS, MAX_LOG_ENTRIES } from "./log-buffer";
import { useLogStream } from "./useLogStream";

export function LogsPanel() {
  const { t } = useTranslation("status");
  const [searchParams] = useSearchParams();
  const [levels, setLevels] = useState<Set<string>>(new Set(LOG_LEVELS));
  const [tag, setTag] = useState("");
  const [keyword, setKeyword] = useState("");
  const [following, setFollowing] = useState(true);
  const [confirmClear, setConfirmClear] = useState(false);
  const [clearing, setClearing] = useState(false);
  const [highlightSeq, setHighlightSeq] = useState<number | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const rowRef = useRef<HTMLDivElement>(null);
  const jumpMsgRef = useRef(searchParams.get("jump"));
  const queryClient = useQueryClient();
  const stream = useLogStream();
  const { logs, paused, pendingCount, connection, togglePause, reconnect, reset } = stream;
  const kw = keyword.trim().toLowerCase();
  const filtered = logs.filter((entry) => levels.has(entry.level) && (!tag || tag === entry.tag) && (!kw || `${entry.message} ${entry.tag}`.toLowerCase().includes(kw)));
  const byLevel: Record<string, number> = {};
  const byTag: Record<string, number> = {};
  logs.forEach((entry) => {
    byLevel[entry.level] = (byLevel[entry.level] ?? 0) + 1;
    if (entry.tag) byTag[entry.tag] = (byTag[entry.tag] ?? 0) + 1;
  });
  const onlyErrors = levels.size === 2 && levels.has("ERROR") && levels.has("CRITICAL");

  useEffect(() => {
    if (!logs.length || !jumpMsgRef.current) return;
    const message = jumpMsgRef.current;
    jumpMsgRef.current = null;
    const target = [...logs].reverse().find((entry) => entry.message === message);
    if (target) { setFollowing(false); setHighlightSeq(target.seq); }
  }, [logs]);
  useEffect(() => {
    if (highlightSeq === null) return;
    const frame = requestAnimationFrame(() => rowRef.current?.scrollIntoView({ block: "center" }));
    const timer = setTimeout(() => setHighlightSeq(null), 4000);
    return () => { cancelAnimationFrame(frame); clearTimeout(timer); };
  }, [highlightSeq]);
  useEffect(() => {
    if (!following || paused) return;
    const frame = requestAnimationFrame(() => {
      const element = scrollRef.current;
      if (element) element.scrollTop = element.scrollHeight;
    });
    return () => cancelAnimationFrame(frame);
  }, [logs, following, paused, keyword, tag, levels]);
  useEffect(() => {
    const element = scrollRef.current;
    if (!element || !following || paused) return;
    const observer = new ResizeObserver(() => { element.scrollTop = element.scrollHeight; });
    observer.observe(element);
    return () => observer.disconnect();
  }, [following, paused]);

  const clearLogs = async () => {
    setClearing(true);
    try {
      await statusApi.clearLogs();
      reset();
      setFollowing(true);
      setConfirmClear(false);
      void queryClient.invalidateQueries({ queryKey: ["logs"] });
      void queryClient.invalidateQueries({ queryKey: ["logStats"] });
    } catch (error) { toast.error(apiErrorMessage(error, t("logsView.clearFailed"))); }
    finally { setClearing(false); }
  };
  const toggleLevel = (level: string) => setLevels((previous) => {
    const next = new Set(previous);
    if (next.has(level)) next.delete(level); else next.add(level);
    return next;
  });

  return <section className="logs-console" aria-label={t("logs")}>
    <header className="logs-console-heading">
      <div><Radio size={17} /><h2>{t("logsView.title")}</h2><span className="logs-connection" data-state={paused ? "paused" : connection}>{paused ? t("paused") : connection === "live" ? t("realtime") : t(`logsView.${connection}`)}</span></div>
      <span className="logs-buffer-count">{t("logsView.bufferUsage", { used: logs.length, capacity: MAX_LOG_ENTRIES })}</span>
    </header>
    <LogsToolbar levels={levels} onToggleLevel={toggleLevel} byLevel={byLevel} onlyErrors={onlyErrors}
      onToggleOnlyErrors={() => setLevels(new Set(onlyErrors ? LOG_LEVELS : ["ERROR", "CRITICAL"]))}
      tag={tag} onTagChange={setTag} tagOptions={Object.keys(byTag).sort()} byTag={byTag}
      keyword={keyword} onKeywordChange={setKeyword} paused={paused} onTogglePause={togglePause}
      pendingCount={pendingCount} following={following} onFollow={() => { if (paused) togglePause(); setFollowing(true); }}
      onClear={() => setConfirmClear(true)} />
    {connection === "reconnecting" && <div className="logs-reconnect"><span>{t("logsView.connectionLost")}</span><Button variant="ghost" size="sm" onClick={reconnect}><RefreshCw size={13} />{t("common:retry")}</Button></div>}
    <LogList filtered={filtered} keyword={kw} scrollRef={scrollRef}
      onScroll={() => { const element = scrollRef.current; if (element) setFollowing(element.scrollHeight - element.scrollTop - element.clientHeight < 48); }}
      highlightSeq={highlightSeq} rowRef={rowRef} loading={connection === "connecting" && !logs.length} onTagClick={setTag} />
    <footer className="logs-console-footer"><span>{t("logsView.visibleCount", { count: filtered.length, total: logs.length })}</span><span>{paused ? t("logsView.pauseHint") : following ? t("logsView.following") : t("logsView.unfollowed")}</span></footer>
    <ConfirmDialog open={confirmClear} onClose={() => { if (!clearing) setConfirmClear(false); }} onConfirm={clearLogs} loading={clearing}
      title={t("logsView.clear")} message={t("logsView.clearConfirm")} confirmText={t("logsView.clear")} cancelText={t("common:cancel")} danger />
  </section>;
}
