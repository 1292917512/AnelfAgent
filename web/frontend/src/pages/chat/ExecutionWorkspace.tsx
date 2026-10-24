import { useCallback, useContext, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { Activity, ArrowDown, ArrowUp, Trash2, Pause, Radio } from "lucide-react";
import { useTranslation } from "react-i18next";
import { useActivityStore } from "@/stores/activity-store";
import { useActivityViewStore } from "@/stores/activity-view-store";
import { useShallow } from "zustand/react/shallow";
import { hasActiveWork } from "@/lib/chat-activity";
import { QueryError } from "@/components/common/AsyncState";
import { Button } from "@/components/ui";
import type { ActivityRun } from "@/lib/types/activity";
import { ActivityRunCard } from "./activity/ActivityRunCard";
import { useActivityFollow } from "./activity/useActivityFollow";
import { activityFamilies, activityPages } from "./activity/activity-pages";
import { ActivityStream } from "./activity/ActivityStream";
import { activitySegments } from "./activity/activity-stream";
import { ActivityDetailsProvider } from "./activity/ActivityDetailsProvider";
import { ActivityInspection } from "@/components/common/ActivityInspection";
import { ActivityRoundSummary } from "./activity/ActivityRoundSummary";
import { ActivityStageRail } from "./activity/ActivityStageRail";
import { ActivitySidePanel } from "./activity/ActivitySidePanel";
import { ActivityExecutionControl } from "./activity/ActivityExecutionControl";
import { ActivityConnection } from "./activity/ActivityConnection";
import { ActivityRuntime } from "./activity/ActivityRuntime";

/** 当前执行轮次优先展示；更早的过程只在主动上翻时挂载。 */
export default function ExecutionWorkspace() {
  const { t } = useTranslation("workbench");
  const { epoch, runs, loaded, error, load, clearing, clearHistory } = useActivityStore(useShallow((state) => ({
    epoch: state.epoch, runs: state.runs, loaded: state.loaded, error: state.error, load: state.load,
    clearing: state.clearing, clearHistory: state.clearHistory,
  })));
  useEffect(() => { if (!loaded) void load(); }, [loaded, load]);
  return <ActivityDetailsProvider><section aria-label={t("execution.region")} className="activity-pane flex h-full min-h-0 flex-col">
    {error != null && <div className="p-3"><QueryError compact error={error} retry={() => void load()} /></div>}
    <ActivityFeed key={epoch} epoch={epoch} runs={runs} clearing={clearing} onClear={() => void clearHistory()} />
  </section></ActivityDetailsProvider>;
}

function ActivityFeed({ epoch, runs, clearing, onClear }: { epoch: string; runs: ActivityRun[]; clearing: boolean; onClear: () => void }) {
  const { t } = useTranslation("workbench");
  const inspect = useContext(ActivityInspection);
  const pages = useMemo(() => activityPages(runs), [runs]);
  const [initial] = useState(() => useActivityViewStore.getState().read(epoch));
  const [selected, setSelected] = useState(initial.selected);
  const [family, setFamily] = useState(initial.family);
  const selectedIndex = pages.findIndex((page) => page.id === selected);
  const index = selectedIndex < 0 ? pages.length - 1 : selectedIndex;
  const page = pages[index];
  const touchStart = useRef<number | null>(null);
  const paging = useRef(false);
  const [expanded, setExpanded] = useState(initial.expanded);
  const view = useRef({ selected, family, expanded });
  useLayoutEffect(() => {
    view.current = { selected, family, expanded };
    const store = useActivityViewStore.getState();
    store.save(epoch, { ...view.current, position: store.read(epoch).position });
  }, [epoch, selected, family, expanded]);
  const activeRuns = runs.filter(hasActiveWork);
  const canClear = useMemo(() => activityFamilies(runs).some((item) => !item.runs.some(hasActiveWork)), [runs]);
  const currentPage = index === pages.length - 1;
  const families = useMemo(() => activityFamilies(page?.runs ?? []), [page]);
  const selectedFamily = families.find((item) => item.id === family);
  const focusFamily = selectedFamily ?? families[families.length - 1];
  const focusRun = focusFamily?.runs.find((run) => run.id === focusFamily.id) ?? focusFamily?.runs[0];
  const visibleRuns = useMemo(() => (selectedFamily?.runs ?? page?.runs ?? []).filter((run) => run.kind !== "hook" || !page?.runs.some((parent) => parent.entries.some((entry) => entry.kind === "hook" && entry.activity_ids?.includes(run.id)))), [selectedFamily, page]);
  const segments = useMemo(() => currentPage ? activitySegments(visibleRuns,
    Object.entries(expanded).filter(([, value]) => value.current && value.open).map(([id]) => id)) : [], [visibleRuns, currentPage, expanded]);
  const pinPage = useCallback(() => { if (page) setSelected(page.id); }, [page]);
  const { scroll, content, following, pause: pauseFollowing, resume, handlers } = useActivityFollow(`${page?.id}:${family}:${Math.max(0, ...visibleRuns.map((run) => run.revision))}`, pinPage, currentPage, initial.position,
    (position) => useActivityViewStore.getState().save(epoch, { ...view.current,
      selected: pages.some((item) => item.id === view.current.selected) ? view.current.selected : null,
      expanded: Object.fromEntries(Object.entries(view.current.expanded).filter(([id]) => runs.some((run) => run.id === id))), position }));
  const toggleRun = useCallback((id: string, open: boolean) => {
    setExpanded((previous) => {
      const retained = Object.fromEntries(Object.entries(previous).filter(([key]) => runs.some((run) => run.id === key)));
      return { ...retained, [id]: { current: currentPage, open } };
    });
  }, [runs, currentPage]);
  const toggleCurrentRun = useCallback((id: string, open: boolean) => { pauseFollowing(); toggleRun(id, open); }, [pauseFollowing, toggleRun]);
  const resumeFollowing = () => { setSelected(null); setExpanded({}); resume(); };
  const openPage = (next: number) => {
    const target = pages[next];
    if (!target) return;
    pauseFollowing(); paging.current = true;
    setSelected(target.id); setFamily(null);
    requestAnimationFrame(() => {
      if (scroll.current) scroll.current.scrollTop = next < index ? scroll.current.scrollHeight : 0;
      paging.current = false;
    });
  };
  const revealRun = useCallback((id: string) => { inspect?.([id]); }, [inspect]);
  const older = () => { if (!paging.current && index > 0) openPage(index - 1); };
  const nestedContent = (target: EventTarget | null) => {
    const element = target instanceof Element ? target.closest("pre, .activity-thought-content, .activity-result-text, .activity-context-detail details > div") : null;
    return element != null && element.scrollHeight > element.clientHeight + 1;
  };
  return <>
    <header className="activity-heading"><div className="activity-heading-title"><Activity size={16} className="text-accent" /><h2>{t("activity.title")}</h2></div>
      {focusRun && focusFamily && <ActivityRoundSummary key={focusRun.id} run={focusRun} runs={focusFamily.runs} onReveal={revealRun} />}
      <div className="activity-heading-controls">
        {focusRun && <ActivityExecutionControl key={focusRun.id} run={focusRun} />}
        <ActivityConnection active={activeRuns.length > 0} />
        <Button variant="ghost" size="sm" className="activity-follow-toggle" aria-label={t(following ? "activity.following" : "activity.followPaused")} aria-pressed={following} title={t(following ? "activity.pauseFollow" : "activity.resumeFollow")} onClick={following ? pauseFollowing : resumeFollowing}>{following ? <Radio size={13} /> : <Pause size={13} />}<span className="activity-control-label">{t(following ? "activity.following" : "activity.followPaused")}</span></Button>
        <Button variant="ghost" size="sm" aria-label={t("activity.clearHistory")} title={t("activity.clearHistoryHint")} disabled={!canClear} loading={clearing} onClick={onClear}><Trash2 size={13} /><span className="activity-control-label">{t("activity.clearHistory")}</span></Button>
      </div>
    </header>
    <ActivityRuntime />
    {families.length > 1 && <nav className="activity-sessions" aria-label={t("activity.sessions")}>
      <button aria-pressed={!selectedFamily} onClick={() => setFamily(null)}>{t("activity.allSessions")}</button>
      {families.map((item) => { const root = item.runs.find((run) => run.id === item.id) ?? item.runs[0]!; return <button key={item.id} aria-pressed={selectedFamily?.id === item.id} title={root.label} onClick={() => setFamily(item.id)}>{root.source.channel || t("activity.system")} · {root.source.target || root.actor || t(`activity.kinds.${root.kind}`, { defaultValue: root.kind })}{root.source.session && ` · ${root.source.session}`}<span>{item.runs.length}</span></button>; })}
    </nav>}
    <div className="activity-workspace-body relative min-h-0 flex-1">
      {currentPage && <ActivityStageRail segments={segments} scroll={scroll} onNavigate={pauseFollowing} />}
      <ActivitySidePanel key={`${page?.id}:${family}`} runs={visibleRuns} work={selectedFamily?.runs ?? runs} />
      <div ref={scroll} className="activity-feed h-full overflow-y-auto overscroll-contain" data-following={following} tabIndex={0} aria-label={t("activity.timeline")}
        {...handlers}
        onWheel={(event) => { handlers.onWheel(event); if (event.target instanceof Node && event.currentTarget.contains(event.target) && event.deltaY < -25 && (scroll.current?.scrollTop ?? 1) <= 0 && !nestedContent(event.target)) older(); }}
        onTouchStart={(event) => { handlers.onTouchStart(event); touchStart.current = event.target instanceof Node && event.currentTarget.contains(event.target) && (scroll.current?.scrollTop ?? 1) <= 0 && !nestedContent(event.target) ? event.touches[0]?.clientY ?? null : null; }}
        onTouchEnd={(event) => { if (touchStart.current != null && (event.changedTouches[0]?.clientY ?? 0) - touchStart.current > 64) older(); touchStart.current = null; handlers.onTouchEnd(); }}>
        <div ref={content}>
        {index > 0 && <button className="activity-history-link" onClick={older}><ArrowUp size={13} />{t("activity.previousRound")}</button>}
        {index < pages.length - 1 && <div className="activity-history-caption"><span>{t("activity.historyRound")}</span><button onClick={() => openPage(index + 1)}>{t("activity.nextRound")} <ArrowDown size={12} /></button></div>}
        {!page && <div className="activity-empty"><span className="welcome-orbit" aria-hidden="true"><span /></span><h3>{t("activity.empty")}</h3><p>{t("activity.emptyHint")}</p></div>}
        {currentPage ? <ActivityStream runs={visibleRuns} segments={segments} onReveal={revealRun} onToggle={toggleCurrentRun} /> : visibleRuns.map((run) => <ActivityRunCard key={run.id} run={run} current={false}
          open={expanded[run.id]?.current === currentPage ? (expanded[run.id]?.open ?? currentPage) : currentPage}
          onToggle={toggleRun} onReveal={revealRun} />)}
        {page && <p className="activity-retention-note">{t("activity.retention")}</p>}
        </div>
      </div>
    </div>
  </>;
}
