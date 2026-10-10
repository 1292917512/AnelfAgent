import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { Activity, ArrowDown, ArrowUp, Trash2 } from "lucide-react";
import { useTranslation } from "react-i18next";
import { useActivityStore } from "@/stores/activity-store";
import { useChatStore } from "@/stores/chat-store";
import { QueryError } from "@/components/common/AsyncState";
import { Button } from "@/components/ui";
import type { ActivityRun } from "@/lib/types/activity";
import { ActivityRunCard } from "./activity/ActivityRunCard";
import { activityPages } from "./activity/activity-pages";

/** 当前执行轮次优先展示；更早的过程只在主动上翻时挂载。 */
export default function ExecutionWorkspace() {
  const { t } = useTranslation("workbench");
  const { epoch, runs, loaded, error, load, clearing, clearHistory } = useActivityStore();
  useEffect(() => { if (!loaded) void load(); }, [loaded, load]);
  return <section aria-label={t("execution.region")} className="activity-pane flex h-full min-h-0 flex-col">
    {error != null && <div className="p-3"><QueryError compact error={error} retry={() => void load()} /></div>}
    <ActivityFeed key={epoch} runs={runs} clearing={clearing} onClear={() => void clearHistory()} />
  </section>;
}

function ActivityFeed({ runs, clearing, onClear }: { runs: ActivityRun[]; clearing: boolean; onClear: () => void }) {
  const { t } = useTranslation("workbench");
  const connected = useChatStore((state) => state.sseConnected);
  const pages = useMemo(() => activityPages(runs), [runs]);
  const [selected, setSelected] = useState<string | null>(null);
  const selectedIndex = pages.findIndex((page) => page.id === selected);
  const index = selectedIndex < 0 ? pages.length - 1 : selectedIndex;
  const page = pages[index];
  const scroll = useRef<HTMLDivElement>(null);
  const follow = useRef(true);
  const reading = useRef(false);
  const touchStart = useRef<number | null>(null);
  const paging = useRef(false);
  const [behind, setBehind] = useState(false);
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const activeRuns = runs.filter((run) => run.status === "running");
  const toggleRun = useCallback((id: string, open: boolean) => {
    setExpanded((current) => Object.fromEntries(runs.map((run) => [run.id, run.id === id ? open : current[run.id] ?? true])));
  }, [runs]);
  const pauseFollowing = () => {
    reading.current = true; follow.current = false; setBehind(true);
    if (page) setSelected(page.id);
  };
  const openPage = (next: number) => {
    const target = pages[next];
    if (!target) return;
    reading.current = true; follow.current = false; paging.current = true;
    setSelected(target.id); setBehind(true);
    requestAnimationFrame(() => {
      if (scroll.current) scroll.current.scrollTop = next < index ? scroll.current.scrollHeight : 0;
      paging.current = false;
    });
  };
  const revealRun = (id: string) => {
    const targetPage = pages.find((item) => item.runs.some((run) => run.id === id));
    if (!targetPage) return;
    toggleRun(id, true); pauseFollowing(); setSelected(targetPage.id);
    requestAnimationFrame(() => {
      const container = scroll.current;
      const target = document.getElementById(`activity-${id}`);
      if (container && target) container.scrollTo({ top: container.scrollTop + target.getBoundingClientRect().top - container.getBoundingClientRect().top - 18, behavior: "instant" });
    });
  };
  const older = () => { if (!paging.current && index > 0) openPage(index - 1); };
  const nestedContent = (target: EventTarget | null) => target instanceof Element && target.closest("pre, .activity-thought-content, .activity-result-text, .activity-context-detail details > div");
  useLayoutEffect(() => {
    if (follow.current && scroll.current) scroll.current.scrollTop = scroll.current.scrollHeight;
  }, [runs, selected]);
  return <>
    <header className="activity-heading"><Activity size={16} className="text-accent" /><h2>{t("activity.title")}</h2>
      <span className="ml-auto inline-flex items-center gap-2 text-xs text-muted" role="status">{activeRuns.length > 0 && <span className="activity-live-dot" />}{t(!connected ? "activity.reconnecting" : activeRuns.length ? "activity.working" : "activity.idle")}</span>
      <Button variant="ghost" size="sm" title={t("activity.clearHistoryHint")} disabled={pages.length < 2} loading={clearing} onClick={onClear}><Trash2 size={13} />{t("activity.clearHistory")}</Button>
    </header>
    {activeRuns.length > 1 && <nav className="activity-active-runs" aria-label={t("activity.activeRuns")}>
      {activeRuns.map((run) => <button key={run.id} title={run.label} onClick={() => revealRun(run.id)}>
        <span className="activity-live-dot" /><span>{run.source.channel || t("activity.system")} · {run.label || t(`activity.kinds.${run.kind}`, { defaultValue: run.kind })}</span>
      </button>)}
    </nav>}
    <div className="relative min-h-0 flex-1">
      <div ref={scroll} className="activity-feed h-full overflow-y-auto overscroll-contain"
        onWheel={(event) => { if (event.deltaY < -25 && (scroll.current?.scrollTop ?? 1) <= 0 && !nestedContent(event.target)) older(); }}
        onTouchStart={(event) => { touchStart.current = (scroll.current?.scrollTop ?? 1) <= 0 && !nestedContent(event.target) ? event.touches[0]?.clientY ?? null : null; }}
        onTouchEnd={(event) => { if (touchStart.current != null && (event.changedTouches[0]?.clientY ?? 0) - touchStart.current > 64) older(); touchStart.current = null; }}
        onClickCapture={(event) => { if (event.target instanceof Element && event.target.closest("button, summary, a")) pauseFollowing(); }}
        onScroll={() => {
          const element = scroll.current;
          if (!element || paging.current) return;
          follow.current = !reading.current && element.scrollHeight - element.scrollTop - element.clientHeight < 100;
          if (!follow.current && !reading.current) pauseFollowing();
          setBehind(!follow.current);
        }}>
        {index > 0 && <button className="activity-history-link" onClick={older}><ArrowUp size={13} />{t("activity.previousRound")}</button>}
        {index < pages.length - 1 && <div className="activity-history-caption"><span>{t("activity.historyRound")}</span><button onClick={() => openPage(index + 1)}>{t("activity.nextRound")} <ArrowDown size={12} /></button></div>}
        {!page && <div className="activity-empty"><span className="welcome-orbit" aria-hidden="true"><span /></span><h3>{t("activity.empty")}</h3><p>{t("activity.emptyHint")}</p></div>}
        {page?.runs.map((run) => <ActivityRunCard key={run.id} run={run} open={expanded[run.id] ?? true} onToggle={toggleRun} onReveal={revealRun} />)}
        {page && <p className="activity-retention-note">{t("activity.retention")}</p>}
      </div>
      {(behind || index < pages.length - 1) && <Button className="activity-follow" size="sm" onClick={() => {
        reading.current = false; follow.current = true; setBehind(false); setSelected(null);
        scroll.current?.scrollTo({ top: scroll.current.scrollHeight, behavior: "instant" });
      }}><ArrowDown size={14} />{t("activity.latest")}</Button>}
    </div>
  </>;
}
