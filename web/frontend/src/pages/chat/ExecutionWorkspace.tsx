import { useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { Activity, ArrowDown, ArrowUp, Trash2, Pause, Radio } from "lucide-react";
import { useTranslation } from "react-i18next";
import { useActivityStore } from "@/stores/activity-store";
import { useChatStore } from "@/stores/chat-store";
import { QueryError } from "@/components/common/AsyncState";
import { Button } from "@/components/ui";
import type { ActivityRun } from "@/lib/types/activity";
import { ActivityRunCard } from "./activity/ActivityRunCard";
import { useActivityFollow } from "./activity/useActivityFollow";
import { activityPages } from "./activity/activity-pages";
import { ActivityDetailsProvider } from "./activity/ActivityDetailsProvider";
import { ActivityInspection } from "@/components/common/ActivityInspection";

/** 当前执行轮次优先展示；更早的过程只在主动上翻时挂载。 */
export default function ExecutionWorkspace() {
  const { t } = useTranslation("workbench");
  const { epoch, runs, loaded, error, load, clearing, clearHistory } = useActivityStore();
  useEffect(() => { if (!loaded) void load(); }, [loaded, load]);
  return <ActivityDetailsProvider><section aria-label={t("execution.region")} className="activity-pane flex h-full min-h-0 flex-col">
    {error != null && <div className="p-3"><QueryError compact error={error} retry={() => void load()} /></div>}
    <ActivityFeed key={epoch} runs={runs} clearing={clearing} onClear={() => void clearHistory()} />
  </section></ActivityDetailsProvider>;
}

function ActivityFeed({ runs, clearing, onClear }: { runs: ActivityRun[]; clearing: boolean; onClear: () => void }) {
  const { t } = useTranslation("workbench");
  const inspect = useContext(ActivityInspection);
  const connected = useChatStore((state) => state.sseConnected);
  const pages = useMemo(() => activityPages(runs), [runs]);
  const [selected, setSelected] = useState<string | null>(null);
  const selectedIndex = pages.findIndex((page) => page.id === selected);
  const index = selectedIndex < 0 ? pages.length - 1 : selectedIndex;
  const page = pages[index];
  const touchStart = useRef<number | null>(null);
  const paging = useRef(false);
  const [expanded, setExpanded] = useState<Record<string, { current: boolean; open: boolean }>>({});
  const activeRuns = runs.filter((run) => run.status === "running");
  const currentPage = index === pages.length - 1;
  const visibleRuns = page?.runs.filter((run) => run.kind !== "hook" || !page.runs.some((parent) => parent.entries.some((entry) => entry.kind === "hook" && entry.activity_ids?.includes(run.id)))) ?? [];
  const latestRun = visibleRuns.reduce<ActivityRun | undefined>((latest, run) => !latest || run.updated_at >= latest.updated_at ? run : latest, undefined);
  const pinPage = useCallback(() => { if (page) setSelected(page.id); }, [page]);
  const { scroll, content, following, pause: pauseFollowing, resume, handlers } = useActivityFollow(latestRun?.id, latestRun?.revision ?? 0, pinPage);
  const toggleRun = useCallback((id: string, open: boolean) => {
    setExpanded((previous) => {
      const retained = Object.fromEntries(Object.entries(previous).filter(([key]) => runs.some((run) => run.id === key)));
      return { ...retained, [id]: { current: currentPage, open } };
    });
  }, [runs, currentPage]);
  const resumeFollowing = () => { setSelected(null); setExpanded({}); resume(); };
  const openPage = (next: number) => {
    const target = pages[next];
    if (!target) return;
    pauseFollowing(); paging.current = true;
    setSelected(target.id);
    requestAnimationFrame(() => {
      if (scroll.current) scroll.current.scrollTop = next < index ? scroll.current.scrollHeight : 0;
      paging.current = false;
    });
  };
  const revealRun = (id: string) => {
    if (runs.some((run) => run.id === id && run.kind === "hook") && inspect) { inspect([id]); return; }
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
  const nestedContent = (target: EventTarget | null) => {
    const element = target instanceof Element ? target.closest("pre, .activity-thought-content, .activity-result-text, .activity-context-detail details > div") : null;
    return element != null && element.scrollHeight > element.clientHeight + 1;
  };
  return <>
    <header className="activity-heading"><div className="activity-heading-title"><Activity size={16} className="text-accent" /><h2>{t("activity.title")}</h2></div>
      <span className="activity-connection ml-auto inline-flex items-center gap-2 text-xs text-muted" role="status">{activeRuns.length > 0 && <span className="activity-live-dot" />}{t(!connected ? "activity.reconnecting" : activeRuns.length ? "activity.working" : "activity.idle")}</span>
      <Button variant="ghost" size="sm" className="activity-follow-toggle" aria-pressed={following} title={t(following ? "activity.pauseFollow" : "activity.resumeFollow")} onClick={following ? pauseFollowing : resumeFollowing}>{following ? <Radio size={13} /> : <Pause size={13} />}{t(following ? "activity.following" : "activity.followPaused")}</Button>
      <Button variant="ghost" size="sm" title={t("activity.clearHistoryHint")} disabled={pages.length < 2} loading={clearing} onClick={onClear}><Trash2 size={13} />{t("activity.clearHistory")}</Button>
    </header>
    {activeRuns.length > 1 && <nav className="activity-active-runs" aria-label={t("activity.activeRuns")}>
      {activeRuns.map((run) => <button key={run.id} title={run.label} onClick={() => revealRun(run.id)}>
        <span className="activity-live-dot" /><span>{run.source.channel || t("activity.system")} · {run.label || t(`activity.kinds.${run.kind}`, { defaultValue: run.kind })}</span>
      </button>)}
    </nav>}
    <div className="relative min-h-0 flex-1">
      <div ref={scroll} className="activity-feed h-full overflow-y-auto overscroll-contain" data-following={following} tabIndex={0} aria-label={t("activity.timeline")}
        {...handlers}
        onWheel={(event) => { handlers.onWheel(event); if (event.target instanceof Node && event.currentTarget.contains(event.target) && event.deltaY < -25 && (scroll.current?.scrollTop ?? 1) <= 0 && !nestedContent(event.target)) older(); }}
        onTouchStart={(event) => { handlers.onTouchStart(event); touchStart.current = event.target instanceof Node && event.currentTarget.contains(event.target) && (scroll.current?.scrollTop ?? 1) <= 0 && !nestedContent(event.target) ? event.touches[0]?.clientY ?? null : null; }}
        onTouchEnd={(event) => { if (touchStart.current != null && (event.changedTouches[0]?.clientY ?? 0) - touchStart.current > 64) older(); touchStart.current = null; handlers.onTouchEnd(); }}>
        <div ref={content}>
        {index > 0 && <button className="activity-history-link" onClick={older}><ArrowUp size={13} />{t("activity.previousRound")}</button>}
        {index < pages.length - 1 && <div className="activity-history-caption"><span>{t("activity.historyRound")}</span><button onClick={() => openPage(index + 1)}>{t("activity.nextRound")} <ArrowDown size={12} /></button></div>}
        {!page && <div className="activity-empty"><span className="welcome-orbit" aria-hidden="true"><span /></span><h3>{t("activity.empty")}</h3><p>{t("activity.emptyHint")}</p></div>}
        {visibleRuns.map((run) => <ActivityRunCard key={run.id} run={run} current={currentPage}
          open={expanded[run.id]?.current === currentPage ? (expanded[run.id]?.open ?? currentPage) : currentPage}
          onToggle={toggleRun} onReveal={revealRun} />)}
        {page && <p className="activity-retention-note">{t("activity.retention")}</p>}
        </div>
      </div>
    </div>
  </>;
}
